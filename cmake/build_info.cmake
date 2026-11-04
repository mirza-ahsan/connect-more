# =============================================================================
#  Writes generated/cm/build_info.hpp from its template, filling in the current
#  git commit and whether the working tree differs from it.
#
#  Runs at configure time and again before every build (see CMakeLists.txt,
#  section 6), so the header always describes the code that is being compiled.
#  The file is only rewritten when its content changes, so an unchanged state
#  does not cause anything to recompile.
#
#  Inputs (-D): GIT_EXECUTABLE (may be empty), SOURCE_DIR, TEMPLATE, OUTPUT
# =============================================================================

set(CM_GIT_COMMIT "unknown")
set(CM_GIT_DIRTY "unknown")

if(GIT_EXECUTABLE)
  execute_process(
    COMMAND "${GIT_EXECUTABLE}" rev-parse --short=12 HEAD
    WORKING_DIRECTORY "${SOURCE_DIR}"
    OUTPUT_VARIABLE _commit
    OUTPUT_STRIP_TRAILING_WHITESPACE
    RESULT_VARIABLE _rc
    ERROR_QUIET)
  if(_rc EQUAL 0)
    set(CM_GIT_COMMIT "${_commit}")

    # Untracked files count as changes too: a new .cpp file is compiled in
    # (the solver build finds sources by glob) before it is ever committed.
    # --no-optional-locks keeps this read-only check from locking the index.
    execute_process(
      COMMAND "${GIT_EXECUTABLE}" --no-optional-locks status --porcelain --untracked-files=normal
      WORKING_DIRECTORY "${SOURCE_DIR}"
      OUTPUT_VARIABLE _status
      RESULT_VARIABLE _rc
      ERROR_QUIET)
    if(_rc EQUAL 0)
      if(_status STREQUAL "")
        set(CM_GIT_DIRTY "false")
      else()
        set(CM_GIT_DIRTY "true")
      endif()
    endif()
  endif()
endif()

configure_file("${TEMPLATE}" "${OUTPUT}" @ONLY)
