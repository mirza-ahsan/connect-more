"""Tests for scripts/unsplit_count.py and the build information in common/build.py and
scripts/helpers.py."""

from pathlib import Path

from common.build import git_state, parse_commit
from common.messages import Outcome
from scripts import helpers, unsplit_count
from tests.helpers import needs_solver, read_log


def test_parse_result() -> None:
    output = "# board 7x6\nresult 751321 win, 20567582 positions searched, 1.583676 s\n"
    assert unsplit_count.parse_result(output) == {
        "moves": "751321",
        "outcome": "win",
        "positions": 20567582,
        "seconds": 1.583676,
    }
    assert unsplit_count.parse_result("error - bad move\n") is None


def test_build_information() -> None:
    assert parse_commit("commit 2968239fec5a, dirty false") == ("2968239fec5a", False)
    assert parse_commit("commit 2968239fec5a, dirty true") == ("2968239fec5a", True)
    assert parse_commit("board 7x6") is None
    state = git_state()
    assert isinstance(state["commit"], str) and isinstance(state["dirty"], bool)


@needs_solver("7x6")
def test_unsplit_count_keeps_the_solver_output(tmp_path: Path) -> None:
    assert unsplit_count.run("751321", Outcome.WIN, helpers.SOLVER, tmp_path, "u1") == 0
    raw = (tmp_path / "u1/unsplit.txt").read_text(encoding="utf-8")
    assert raw.startswith("# c4solve") and "# threads 1" in raw and "mode single 751321" in raw
    start, end = read_log(tmp_path / "u1/unsplit.jsonl")
    assert "board 7x6" in start["build"]["solver_header"]
    assert end["correct"] is True and end["result"]["positions"] > 0

    # A wrong expected result fails (Pons End-Easy: a loss for the player to move, solved fast).
    loss = "2252576253462244111563365343671351441"
    assert unsplit_count.run(loss, Outcome.WIN, helpers.SOLVER, tmp_path, "u2") == 1
