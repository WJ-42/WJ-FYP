from openhands.runtime.plugins.agent_skills.file_ops.file_ops import append_file
def test_append_file(tmp_path):
    p = tmp_path / "t.txt"; p.write_text("first\n")
    append_file(str(p), "second")
    assert "second" in p.read_text()
