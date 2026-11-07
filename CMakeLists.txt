# =============================================================================
#  connect-more: distributed Connect Four solver for unequal consumer machines
#  Top-level CMake build
# =============================================================================
#
#  Quick start (from the repo root):
#      cmake --workflow --preset release        # configure + build + test
#  Or step by step:
#      cmake --preset release
#      cmake --build --preset release
#      ctest --preset release
#
#  Binaries land in:  build/<preset>/bin/
#
#  The toolchain is locked (see ENVIRONMENT.md):
#      Omarchy (Arch Linux) packages: CMake 4.4.4 | Ninja | GCC 16.2.1
#  Experiment builds MUST use the "release" preset on both machines so both run
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
  DESCRIPTION "Distributed Connect Four solver for heterogeneous consumer machines"
  HOMEPAGE_URL "https://github.com/mirza-ahsan/connect-more"
  LANGUAGES CXX)

# -----------------------------------------------------------------------------
#  1. Project options
# -----------------------------------------------------------------------------
option(CM_STRICT_TOOLCHAIN
  "Fail if the compiler is not the locked GCC version (keep ON for experiments)" ON)
set(CM_EXPECTED_GCC_VERSION "16.2.1" CACHE STRING
  "Locked GCC version, see ENVIRONMENT.md")
set(CM_ARCH "x86-64-v3" CACHE STRING
  "Value for -march. Must be supported by BOTH machines (x86-64-v3 = AVX2 level)")
option(CM_STATIC
  "Link executables fully statically (no shared libraries needed at runtime)" ON)
option(CM_WARNINGS_AS_ERRORS
  "Treat compiler warnings as errors" OFF)
option(CM_ENABLE_LTO
  "Enable link-time optimisation for Release builds" OFF)
option(CM_ENABLE_UBSAN
  "Trap on undefined behaviour in Debug builds (trap mode, no runtime library)" OFF)
set(CM_BOARD_VARIANTS "7x6" CACHE STRING
  "Semicolon-separated board sizes (WIDTHxHEIGHT) to build, e.g. 7x6;6x7;6x5")
set(CM_DEFAULT_TT_MB "1024" CACHE STRING
  "Default transposition table size in MB. Keep identical on both machines")

option(BUILD_TESTING "Build C++ unit and verification tests" ON)
if(BUILD_TESTING)
  enable_testing()   # plain CTest, without CDash dashboard targets
endif()

# -----------------------------------------------------------------------------
#  2. Toolchain verification (this is what "locks" the C++ environment)
# -----------------------------------------------------------------------------
if(NOT CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
  message(FATAL_ERROR
    "This project is locked to GCC (the Omarchy / Arch Linux gcc package).\n"
    "Detected compiler: ${CMAKE_CXX_COMPILER_ID} (${CMAKE_CXX_COMPILER})")
endif()

if(NOT CMAKE_CXX_COMPILER_VERSION VERSION_EQUAL CM_EXPECTED_GCC_VERSION)
  set(_cm_msg "Expected GCC ${CM_EXPECTED_GCC_VERSION}, found ${CMAKE_CXX_COMPILER_VERSION} at ${CMAKE_CXX_COMPILER}.")
  if(CM_STRICT_TOOLCHAIN)
    message(FATAL_ERROR
      "${_cm_msg}\n"
      "Install the locked gcc package (ENVIRONMENT.md), or configure with "
      "-DCM_STRICT_TOOLCHAIN=OFF for development only (never for experiment builds).")
  else()
    message(WARNING "${_cm_msg} Builds will NOT be comparable across machines.")
  endif()
endif()

if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "A 64-bit compiler is required (bitboards use 64-bit integers).")
endif()

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
  message(WARNING
    "Experiments run on Linux (Omarchy). Builds on ${CMAKE_SYSTEM_NAME} are for development only.")
endif()

if(NOT CMAKE_GENERATOR MATCHES "Ninja")
  message(WARNING
    "Generator '${CMAKE_GENERATOR}' is not the locked generator (Ninja). "
    "Use the presets: cmake --preset release")
endif()

# std::thread needs GCC's POSIX thread model (GCC on Linux has it).
execute_process(
  COMMAND "${CMAKE_CXX_COMPILER}" -v
  ERROR_VARIABLE _cm_gcc_v
  OUTPUT_QUIET)
if(_cm_gcc_v MATCHES "Thread model: ([a-z0-9]+)")
  set(CM_THREAD_MODEL "${CMAKE_MATCH_1}")
  if(NOT CM_THREAD_MODEL STREQUAL "posix")
    message(FATAL_ERROR
      "GCC thread model is '${CM_THREAD_MODEL}', but 'posix' is required for std::thread.\n"
      "Use a GCC built with POSIX threads (the Omarchy / Arch Linux gcc package).")
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

# Code generation. -mtune=generic avoids tuning for one machine's CPU, keeping
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
  "$<$<CONFIG:Debug>:_GLIBCXX_ASSERTIONS>")

# Undefined-behaviour checks in trap mode: compile-only flags, so no libubsan
# is needed at link time, and the static link keeps working.
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
#
#     The git commit and the dirty flag are checked before every build by
#     cmake/build_info.cmake, not only at configure time, so the header never
#     claims "not dirty" for code with uncommitted or untracked changes.
# -----------------------------------------------------------------------------
find_package(Git QUIET)

string(TIMESTAMP CM_CONFIGURE_TIME "%Y-%m-%dT%H:%M:%SZ" UTC)
string(TOUPPER "${CMAKE_BUILD_TYPE}" _cm_bt_upper)
set(CM_CONFIG_FLAGS "${CMAKE_CXX_FLAGS} ${CMAKE_CXX_FLAGS_${_cm_bt_upper}}")
string(STRIP "${CM_CONFIG_FLAGS}" CM_CONFIG_FLAGS)

# Template with every value filled in except the two git ones, which keep their
# placeholders for cmake/build_info.cmake.
set(CM_GIT_COMMIT "@CM_GIT_COMMIT@")
set(CM_GIT_DIRTY  "@CM_GIT_DIRTY@")
set(_cm_build_info_template "${PROJECT_BINARY_DIR}/generated/build_info.hpp.in")
set(_cm_build_info_header   "${PROJECT_BINARY_DIR}/generated/cm/build_info.hpp")

file(CONFIGURE
  OUTPUT "${_cm_build_info_template}"
  CONTENT [=[
// Generated by CMake (git values refreshed before every build). Do not edit.
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

set(_cm_build_info_args
  "-DGIT_EXECUTABLE=${GIT_EXECUTABLE}"
  "-DSOURCE_DIR=${PROJECT_SOURCE_DIR}"
  "-DTEMPLATE=${_cm_build_info_template}"
  "-DOUTPUT=${_cm_build_info_header}"
  -P "${PROJECT_SOURCE_DIR}/cmake/build_info.cmake")

# Once now, so the header exists for the editors before the first build ...
execute_process(COMMAND "${CMAKE_COMMAND}" ${_cm_build_info_args} COMMAND_ERROR_IS_FATAL ANY)

# ... and again before every build. Everything links cm::options, so nothing
# compiles before the header is up to date.
add_custom_target(cm_build_info ALL
  COMMAND "${CMAKE_COMMAND}" ${_cm_build_info_args}
  BYPRODUCTS "${_cm_build_info_header}"
  COMMENT "Checking git commit and uncommitted changes for build_info.hpp"
  VERBATIM)
add_dependencies(cm_options cm_build_info)

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
