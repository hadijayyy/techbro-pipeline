import ast
from pathlib import Path


PIPELINE = Path(__file__).parents[1] / "pipeline-v3.py"


def system_prompt():
    tree = ast.parse(PIPELINE.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SYSTEM_PROMPT"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("SYSTEM_PROMPT missing")


def test_system_prompt_is_source_only_article_to_action_contract():
    prompt = system_prompt()
    assert "satu ISI ARTIKEL" in prompt
    assert "ISI ARTIKEL" in prompt
    assert "Jangan menambah dampak, profesi, angka, skenario, penilaian" in prompt
    assert "Setiap slide wajib kontras" not in prompt
    assert '"post_1"' in prompt
    assert '"post_6"' in prompt
    assert '"error"' in prompt
    assert "gua–lu" in prompt


def test_system_prompt_does_not_embed_fictional_facts_as_examples():
    prompt = system_prompt()
    assert "Pelita Air" not in prompt
    assert "Garuda" not in prompt
    assert "10.000 karyawan" not in prompt


def test_article_to_action_keeps_runtime_post_contract():
    prompt = system_prompt()
    for slide in range(1, 7):
        assert f'"post_{slide}"' in prompt
    assert '"status"' in prompt
    assert '"error"' in prompt
