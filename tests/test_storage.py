from pathlib import Path

from fusion_reviewer.storage import build_paper_display_name


def test_build_paper_display_name_prefers_title_over_generic_source_name():
    display = build_paper_display_name("测试论文", "paper.docx")
    assert display == "测试论文"


def test_build_paper_display_name_falls_back_to_run_label_when_inputs_are_garbled():
    run_dir = Path(
        r"D:\auto reviewer system\fusion-reviewer\review_outputs\20260328-030001__8-投稿-中国网络游戏沉迷防治政策-主题演进-府际关系及工具选择-1-1__f3a41984b1"
    )
    display = build_paper_display_name(
        "????????????????",
        "8.????????????????(1)(1).docx",
        run_dir,
    )
    assert display == "8-投稿-中国网络游戏沉迷防治政策-主题演进-府际关系及工具选择-1-1"
