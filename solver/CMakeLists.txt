# =============================================================================
#  solver: C++ core library, executables and tests, built once per board size
# =============================================================================
#
#  For every WIDTHxHEIGHT in CM_BOARD_VARIANTS (e.g. 7x6) this builds:
#      c4core_7x6          static library from src/*.cpp
#      <app>_7x6           one executable per apps/<app>.cpp
#      test_<name>_7x6     one unit test per tests/test_<name>.cpp  (label: unit)
#      verify_<data>_7x6   c4solve_7x6 --verify tests/data/7x6/<data>.txt (label: verify)
#
#  The board size reaches the code as the macros C4_WIDTH and C4_HEIGHT.
#  Files are found by glob, so adding a new .cpp only needs a re-configure
#  (CONFIGURE_DEPENDS makes the build do that automatically).
#  Folders that are still empty are skipped.
# =============================================================================

file(GLOB _c4_core_sources CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp")
file(GLOB _c4_app_sources  CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/apps/*.cpp")
file(GLOB _c4_test_sources CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_*.cpp")

if(NOT CM_BOARD_VARIANTS)
  message(FATAL_ERROR "CM_BOARD_VARIANTS is empty; give at least one size, e.g. 7x6.")
endif()

foreach(_variant IN LISTS CM_BOARD_VARIANTS)
  if(NOT _variant MATCHES "^([0-9]+)x([0-9]+)$")
    message(FATAL_ERROR "Bad board variant '${_variant}'; expected WIDTHxHEIGHT, e.g. 7x6.")
  endif()
  set(_width  "${CMAKE_MATCH_1}")
  set(_height "${CMAKE_MATCH_2}")

  # --- Core library (INTERFACE until src/ has sources) -----------------------
  set(_core c4core_${_variant})
  if(_c4_core_sources)
    add_library(${_core} STATIC ${_c4_core_sources})
    set(_scope PUBLIC)
  else()
    add_library(${_core} INTERFACE)
    set(_scope INTERFACE)
  endif()
  target_include_directories(${_core} ${_scope}
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${PROJECT_BINARY_DIR}/generated")
  target_compile_definitions(${_core} ${_scope}
    C4_WIDTH=${_width}
    C4_HEIGHT=${_height}
    C4_VARIANT="${_variant}")
  target_link_libraries(${_core} ${_scope} cm::options)

  # --- Executables ------------------------------------------------------------
  foreach(_src IN LISTS _c4_app_sources)
    get_filename_component(_name "${_src}" NAME_WE)
    add_executable(${_name}_${_variant} "${_src}")
    target_link_libraries(${_name}_${_variant} PRIVATE ${_core})
  endforeach()

  if(BUILD_TESTING)
    # --- Unit tests (exit code 0 = pass) -------------------------------------
    foreach(_src IN LISTS _c4_test_sources)
      get_filename_component(_name "${_src}" NAME_WE)
      add_executable(${_name}_${_variant} "${_src}")
      target_link_libraries(${_name}_${_variant} PRIVATE ${_core})
      add_test(NAME ${_name}_${_variant} COMMAND ${_name}_${_variant})
      set_tests_properties(${_name}_${_variant} PROPERTIES LABELS "unit")
    endforeach()

    # --- Verification against known positions (needs apps/c4solve.cpp) -------
    # Each board size has its own data folder, because known results only hold
    # for the size they were computed on.
    file(GLOB _c4_verify_data CONFIGURE_DEPENDS
      "${CMAKE_CURRENT_SOURCE_DIR}/tests/data/${_variant}/*.txt")
    if(TARGET c4solve_${_variant})
      foreach(_data IN LISTS _c4_verify_data)
        get_filename_component(_name "${_data}" NAME_WE)
        add_test(NAME verify_${_name}_${_variant}
          COMMAND c4solve_${_variant} --verify "${_data}")
        set_tests_properties(verify_${_name}_${_variant} PROPERTIES LABELS "verify")
      endforeach()
    endif()
  endif()
endforeach()
