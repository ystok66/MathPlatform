from pathlib import Path


def test_practice_question_draft_is_keyed_by_question_and_state_version():
    page = Path("apps/web/app/page.tsx").read_text(encoding="utf-8")

    assert 'const PRACTICE_QUESTION_DRAFT_PREFIX = "math-platform-question-draft-v1"' in page
    assert "type PracticeQuestionDraft" in page
    assert "practiceQuestionDraftKey(activeLearnerId, sessionId, selectedQuestion, currentQuestionStateVersion)" in page
    assert "state_version: currentQuestionStateVersion" in page
    assert "setTimeout(() =>" in page
    assert "}, 350)" in page
    assert "草稿不计分" in page


def test_practice_question_draft_status_and_cleanup_are_visible():
    page = Path("apps/web/app/page.tsx").read_text(encoding="utf-8")
    styles = Path("apps/web/app/styles.css").read_text(encoding="utf-8")

    assert "questionDraftStatusText" in page
    assert 'className={`draftStatus ${questionDraftStatus.includes("失败") ? "error" : ""}`}' in page
    assert 'clearCurrentQuestionDraft("已提交，草稿已清理")' in page
    assert 'clearCurrentQuestionDraft("已跳过，草稿已清理")' in page
    assert 'clearCurrentQuestionDraft("已看答案，草稿已清理")' in page
    assert 'clearCurrentQuestionDraft("题目已更换，原题草稿已清理")' in page
    assert ".draftStatus" in styles

