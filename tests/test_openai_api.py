from webchat.openai_api import completion, fold, text_of


def test_single_user_message_goes_as_is():
    assert fold([{"role": "user", "content": "привет"}]) == "привет"


def test_history_is_folded_into_one_prompt():
    p = fold([{"role": "system", "content": "be short"}, {"role": "user", "content": "a"},
              {"role": "assistant", "content": "b"}, {"role": "user", "content": [{"type": "text", "text": "c"}]}])
    assert p.index("### Instructions\nbe short") < p.index("### User\na") < p.index("### Assistant\nb") < p.index("### User\nc")


def test_text_parts_and_completion_shape():
    assert text_of([{"type": "text", "text": "x"}, {"type": "image_url"}]) == "x"
    c = completion("glm", "ok")
    assert c["choices"][0]["message"] == {"role": "assistant", "content": "ok"} and c["object"] == "chat.completion"
