from corpus_pipeline.source_url import is_git_repo_url, jina_reader_url
from corpus_pipeline.commands.acquire_page import slugify


def test_repository_root_classification():
    assert is_git_repo_url("https://github.com/example/project")
    assert is_git_repo_url("https://gitlab.com/example/project.git")
    assert not is_git_repo_url("https://github.com/example/project/issues/1")
    assert not is_git_repo_url("https://example.org/example/project")


def test_reader_wrapper_is_idempotent():
    prefix = "https://reader.example/"
    page = "https://example.org/guide"
    wrapped = prefix + page
    assert jina_reader_url(page, prefix) == wrapped
    assert jina_reader_url(wrapped, prefix) == wrapped


def test_page_slug_is_bounded_and_safe():
    assert slugify("A Guide: Indexes & Search") == "a-guide-indexes-search"
    assert len(slugify("x" * 200)) == 80

