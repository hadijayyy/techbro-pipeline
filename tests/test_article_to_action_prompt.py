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
    assert "Jangan menambah dampak, profesi, angka, skenario, motif, status resmi, timeline, penilaian" in prompt
    assert "Setiap slide wajib kontras" not in prompt
    assert '"post_1"' in prompt
    assert '"post_6"' in prompt
    assert '"error"' in prompt
    assert "gua–lu" in prompt
    assert "Buka dengan fakta paling mahal dan fakta paling kuat" in prompt
    assert "Tegangan hanya boleh datang dari perbandingan atau perubahan yang literal di artikel" in prompt
    assert "Jangan memancing dengan teka-teki" in prompt
    assert "Tidak perlu memaksa satu jenis fakta ke slide tertentu" in prompt
    assert "Jangan pakai label-colon, hashtag, jargon birokratis, template AI" in prompt
    assert "slogan, kalimat motivasi, atau kesimpulan yang terdengar besar" in prompt


def test_system_prompt_does_not_embed_fictional_facts_as_examples():
    prompt = system_prompt()
    assert "Pelita Air" not in prompt
    assert "Garuda" not in prompt
    assert "10.000 karyawan" not in prompt
    assert "Pandu Sjahrir" not in prompt
    assert "Destry" not in prompt


def test_article_to_action_keeps_runtime_post_contract():
    prompt = system_prompt()
    for slide in range(1, 7):
        assert f'"post_{slide}"' in prompt
    assert '"status"' in prompt
    assert '"error"' in prompt
