from elasticsearch_agent import cli


def test_chat_loop_processes_multiple_questions_and_exits(monkeypatch):
    questions = iter(["first question", "", "second question", "quit"])
    output = []

    monkeypatch.setattr(cli, "run_question", lambda question: f"answer to {question}")

    assert cli.chat_loop(lambda _prompt: next(questions), output.append) == 0
    assert output == [
        "Interactive chat started. Type 'exit' or 'quit' to leave.",
        "agent> answer to first question",
        "agent> answer to second question",
        "Goodbye!",
    ]


def test_chat_loop_keeps_running_after_request_failure(monkeypatch):
    questions = iter(["bad question", "exit"])
    output = []

    def run_question(_question):
        raise RuntimeError("temporary failure")

    monkeypatch.setattr(cli, "run_question", run_question)

    assert cli.chat_loop(lambda _prompt: next(questions), output.append) == 0
    assert "Request failed: temporary failure" in output
