# =============================================================================
#  connect-more: distributed Connect Four solver for unequal consumer laptops
#  Top-level CMake build
# =============================================================================
#
#  Quick start (PowerShell, from the repo root):
#      cmake --workflow --preset release        # configure + build + test
#  Or step by step:
#      cmake --preset release
#      cmake --build --preset release
#      ctest --preset release
#
#  Binaries land in:  build/<preset>/bin/
#
#  The toolchain is locked (see ENVIRONMENT.md):
#      CMake 4.4.3 (Kitware) | Ninja 1.13.2 | WinLibs GCC 16.1.0 (UCRT, POSIX threads)
#  Experiment builds MUST use the "release" preset on both laptops so both run
#  identically built code.
# =============================================================================

cmake_minimum_required(VERSION 4.4)

# Refuse in-source builds before project() so no build files land in the repo.
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_BINARY_DIR)
  message(FATAL_ERROR
    "In-source builds are not allowed.\n"
    "Delete CMakeCache.txt and CMakeFiles/ from the repo root, then run:\n"
    "    cmake --preset release")
endif()

project(ConnectMore
  VERSION 0.1.0
  DESCRIPTION "Distributed Connect Four solver for heterogeneous consumer laptops"
  HOMEPAGE_URL "https://github.com/mirza-ahsan/connect-more"
  LANGUAGES CXX)

# -----------------------------------------------------------------------------
#  1. Project options
# -----------------------------------------------------------------------------
option(CM_STRICT_TOOLCHAIN
  "Fail if the compiler is not the locked GCC version (keep ON for experiments)" ON)
set(CM_EXPECTED_GCC_VERSION "16.1.0" CACHE STRING
  "Locked GCC version, see ENVIRONMENT.md")
set(CM_ARCH "x86-64-v3" CACHE STRING
  "Value for -march. Must be supported by BOTH laptops (x86-64-v3 = AVX2 level)")
option(CM_STATIC
  "Link executables fully statically (no MinGW DLLs needed at runtime)" ON)
option(CM_WARNINGS_AS_ERRORS
  "Treat compiler warnings as errors" OFF)
option(CM_ENABLE_LTO
  "Enable link-time optimisation for Release builds" OFF)
option(CM_ENABLE_UBSAN
  "Trap on undefined behaviour in Debug builds (trap mode, no runtime library)" OFF)
set(CM_BOARD_VARIANTS "7x6" CACHE STRING
  "Semicolon-separated board sizes (WIDTHxHEIGHT) to build, e.g. 7x6;6x7;6x5")
set(CM_DEFAULT_TT_MB "1024" CACHE STRING
  "Default transposition table size in MB. Keep identical on both laptops")

option(BUILD_TESTING "Build C++ unit and verification tests" ON)
if(BUILD_TESTING)
  enable_testing()   # plain CTest, without CDash dashboard targets
endif()

# -----------------------------------------------------------------------------
#  2. Toolchain verification (this is what "locks" the C++ environment)
# -----------------------------------------------------------------------------
if(NOT CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
  message(FATAL_ERROR
    "This project is locked to GCC (WinLibs MinGW-w64).\n"
    "Detected compiler: ${CMAKE_CXX_COMPILER_ID} (${CMAKE_CXX_COMPILER})")
endif()

if(NOT CMAKE_CXX_COMPILER_VERSION VERSION_EQUAL CM_EXPECTED_GCC_VERSION)
  set(_cm_msg "Expected GCC ${CM_EXPECTED_GCC_VERSION}, found ${CMAKE_CXX_COMPILER_VERSION} at ${CMAKE_CXX_COMPILER}.")
  if(CM_STRICT_TOOLCHAIN)
    message(FATAL_ERROR
      "${_cm_msg}\n"
      "Put the locked WinLibs mingw64\\bin first on PATH, or configure with "
      "-DCM_STRICT_TOOLCHAIN=OFF for development only (never for experiment builds).")
  else()
    message(WARNING "${_cm_msg} Builds will NOT be comparable across laptops.")
  endif()
endif()

if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "A 64-bit compiler is required (bitboards use 64-bit integers).")
endif()

if(NOT WIN32)
  message(WARNING
    "Experiments run on Windows 11. Non-Windows builds are for development only.")
endif()

if(NOT CMAKE_GENERATOR MATCHES "Ninja")
  message(WARNING
    "Generator '${CMAKE_GENERATOR}' is not the locked generator (Ninja). "
    "Use the presets: cmake --preset release")
endif()

# std::thread needs GCC's POSIX thread model (the WinLibs POSIX build has it).
execute_process(
  COMMAND "${CMAKE_CXX_COMPILER}" -v
  ERROR_VARIABLE _cm_gcc_v
  OUTPUT_QUIET)
if(_cm_gcc_v MATCHES "Thread model: ([a-z0-9]+)")
  set(CM_THREAD_MODEL "${CMAKE_MATCH_1}")
  if(NOT CM_THREAD_MODEL STREQUAL "posix")
    message(FATAL_ERROR
      "GCC thread model is '${CM_THREAD_MODEL}', but 'posix' is required for std::thread.\n"
      "Use the WinLibs POSIX build (BrechtSanders.WinLibs.POSIX.UCRT).")
  endif()
else()
  set(CM_THREAD_MODEL "unknown")
  message(WARNING "Could not detect the GCC thread model.")
endif()

include(CheckCXXCompilerFlag)
check_cxx_compiler_flag("-march=${CM_ARCH}" CM_COMPILER_SUPPORTS_ARCH)
if(NOT CM_COMPILER_SUPPORTS_ARCH)
  message(FATAL_ERROR "The compiler does not accept -march=${CM_ARCH}.")
endif()

# -----------------------------------------------------------------------------
#  3. Build type (single-config generators such as Ninja)
# -----------------------------------------------------------------------------
get_property(_cm_multi_config GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
if(NOT _cm_multi_config)
  if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
  endif()
  set_property(CACHE CMAKE_BUILD_TYPE PROPERTY STRINGS
    Debug Release RelWithDebInfo MinSizeRel)
endif()

# -----------------------------------------------------------------------------
#  4. Global language and output settings
# -----------------------------------------------------------------------------
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)          # -std=c++20, not gnu++20
set(CMAKE_CXX_SCAN_FOR_MODULES OFF)    # no C++20 modules; skip dependency scanning
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)  # compile_commands.json for VS Code / clangd
set(CMAKE_COLOR_DIAGNOSTICS ON)

set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${PROJECT_BINARY_DIR}/bin")
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY "${PROJECT_BINARY_DIR}/lib")
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${PROJECT_BINARY_DIR}/lib")

set(THREADS_PREFER_PTHREAD_FLAG ON)
find_package(Threads REQUIRED)

# -----------------------------------------------------------------------------
#  5. Shared build options (every target links cm::options)
# -----------------------------------------------------------------------------
add_library(cm_options INTERFACE)
add_library(cm::options ALIAS cm_options)

target_compile_features(cm_options INTERFACE cxx_std_20)

# Code generation. -mtune=generic avoids tuning for one laptop's CPU, keeping
# the comparison fair. Release flags come from CMake's GCC default: -O3 -DNDEBUG.
target_compile_options(cm_options INTERFACE
  -march=${CM_ARCH}
  -mtune=generic
  "$<$<CONFIG:Debug>:-O0;-g3;-fno-omit-frame-pointer>"
  "$<$<CONFIG:RelWithDebInfo>:-fno-omit-frame-pointer>")

# Warnings
target_compile_options(cm_options INTERFACE
  -Wall
  -Wextra
  -Wpedantic
  -Wshadow
  -Wcast-align
  -Wnon-virtual-dtor
  -Woverloaded-virtual
  -Wold-style-cast
  -Wnull-dereference
  -Wdouble-promotion
  -Wformat=2
  -Wimplicit-fallthrough
  -Wmisleading-indentation
  -Wduplicated-cond
  -Wduplicated-branches
  -Wlogical-op
  "$<$<BOOL:${CM_WARNINGS_AS_ERRORS}>:-Werror>")

target_compile_definitions(cm_options INTERFACE
  CM_DEFAULT_TT_MB=${CM_DEFAULT_TT_MB}
  "$<$<CONFIG:Debug>:_GLIBCXX_ASSERTIONS>"
  # Windows 10+ API level, and keep <windows.h> from breaking std::min/max.
  "$<$<BOOL:${WIN32}>:_WIN32_WINNT=0x0A00;WINVER=0x0A00;NOMINMAX;WIN32_LEAN_AND_MEAN>")

# Undefined-behaviour checks in trap mode: compile-only flags, so no libubsan
# is needed at link time (MinGW does not ship it).
if(CM_ENABLE_UBSAN)
  target_compile_options(cm_options INTERFACE
    "$<$<CONFIG:Debug>:-fsanitize=undefined;-fsanitize-undefined-trap-on-error>")
endif()

if(CM_STATIC)
  target_link_options(cm_options INTERFACE -static)
endif()

target_link_libraries(cm_options INTERFACE Threads::Threads)

# Link-time optimisation (Release only)
if(CM_ENABLE_LTO)
  include(CheckIPOSupported)
  check_ipo_supported(RESULT _cm_ipo OUTPUT _cm_ipo_msg LANGUAGES CXX)
  if(_cm_ipo)
    set(CMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE ON)
  else()
    message(WARNING "LTO requested but not supported: ${_cm_ipo_msg}")
  endif()
endif()

# -----------------------------------------------------------------------------
#  6. Build provenance header (generated/cm/build_info.hpp)
#     Programs should print these values into every experiment log, so each
#     result can be traced back to the exact commit, compiler and flags.
# -----------------------------------------------------------------------------
set(CM_GIT_COMMIT "unknown")
set(CM_GIT_DIRTY  "unknown")
find_package(Git QUIET)
if(GIT_FOUND)
  execute_process(
    COMMAND "${GIT_EXECUTABLE}" rev-parse --short=12 HEAD
    WORKING_DIRECTORY "${PROJECT_SOURCE_DIR}"
    OUTPUT_VARIABLE _cm_commit
    OUTPUT_STRIP_TRAILING_WHITESPACE
    RESULT_VARIABLE _cm_git_rc
    ERROR_QUIET)
  if(_cm_git_rc EQUAL 0)
    set(CM_GIT_COMMIT "${_cm_commit}")
    execute_process(
      COMMAND "${GIT_EXECUTABLE}" status --porcelain --untracked-files=no
      WORKING_DIRECTORY "${PROJECT_SOURCE_DIR}"
      OUTPUT_VARIABLE _cm_status
      ERROR_QUIET)
    if(_cm_status STREQUAL "")
      set(CM_GIT_DIRTY "false")
    else()
      set(CM_GIT_DIRTY "true")
    endif()
  endif()
endif()

# Re-run configure when the checked-out commit changes, so the header stays current.
if(EXISTS "${PROJECT_SOURCE_DIR}/.git/HEAD")
  set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${PROJECT_SOURCE_DIR}/.git/HEAD")
  file(READ "${PROJECT_SOURCE_DIR}/.git/HEAD" _cm_head)
  if(_cm_head MATCHES "^ref: ([^\r\n]+)")
    set(_cm_ref "${PROJECT_SOURCE_DIR}/.git/${CMAKE_MATCH_1}")
    if(EXISTS "${_cm_ref}")
      set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${_cm_ref}")
    endif()
  endif()
endif()

string(TIMESTAMP CM_CONFIGURE_TIME "%Y-%m-%dT%H:%M:%SZ" UTC)
string(TOUPPER "${CMAKE_BUILD_TYPE}" _cm_bt_upper)
set(CM_CONFIG_FLAGS "${CMAKE_CXX_FLAGS} ${CMAKE_CXX_FLAGS_${_cm_bt_upper}}")
string(STRIP "${CM_CONFIG_FLAGS}" CM_CONFIG_FLAGS)

file(CONFIGURE
  OUTPUT "${PROJECT_BINARY_DIR}/generated/cm/build_info.hpp"
  CONTENT [=[
// Generated by CMake at configure time. Do not edit.
#pragma once

namespace cm::build_info {
inline constexpr const char* project_version  = "@PROJECT_VERSION@";
inline constexpr const char* git_commit       = "@CM_GIT_COMMIT@";
inline constexpr const char* git_dirty        = "@CM_GIT_DIRTY@";
inline constexpr const char* build_type       = "@CMAKE_BUILD_TYPE@";
inline constexpr const char* compiler_id      = "@CMAKE_CXX_COMPILER_ID@";
inline constexpr const char* compiler_version = "@CMAKE_CXX_COMPILER_VERSION@";
inline constexpr const char* thread_model     = "@CM_THREAD_MODEL@";
inline constexpr const char* arch             = "@CM_ARCH@";
inline constexpr const char* config_flags     = "@CM_CONFIG_FLAGS@";
inline constexpr const char* static_link      = "@CM_STATIC@";
inline constexpr const char* lto              = "@CM_ENABLE_LTO@";
inline constexpr const char* configured_at    = "@CM_CONFIGURE_TIME@";
}  // namespace cm::build_info
]=]
  @ONLY)

# -----------------------------------------------------------------------------
#  7. Subprojects
# -----------------------------------------------------------------------------
add_subdirectory(solver)

# -----------------------------------------------------------------------------
#  8. Configuration summary
# -----------------------------------------------------------------------------
message(STATUS "")
message(STATUS "=========== connect-more configuration ===========")
message(STATUS "  Compiler        : ${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}")
message(STATUS "  Compiler path   : ${CMAKE_CXX_COMPILER}")
message(STATUS "  Thread model    : ${CM_THREAD_MODEL}")
message(STATUS "  Generator       : ${CMAKE_GENERATOR}")
message(STATUS "  Build type      : ${CMAKE_BUILD_TYPE}")
message(STATUS "  Config flags    : ${CM_CONFIG_FLAGS}")
message(STATUS "  -march / -mtune : ${CM_ARCH} / generic")
message(STATUS "  Static link     : ${CM_STATIC}")
message(STATUS "  LTO             : ${CM_ENABLE_LTO}")
message(STATUS "  UBSan (trap)    : ${CM_ENABLE_UBSAN}")
message(STATUS "  Board variants  : ${CM_BOARD_VARIANTS}")
message(STATUS "  Default TT size : ${CM_DEFAULT_TT_MB} MB")
message(STATUS "  Tests           : ${BUILD_TESTING}")
message(STATUS "  Git commit      : ${CM_GIT_COMMIT} (dirty: ${CM_GIT_DIRTY})")
message(STATUS "==================================================")
message(STATUS "")
