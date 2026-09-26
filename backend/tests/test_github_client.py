import httpx
import pytest
import respx

from app import github_client as gh
from app.github_client import (
    BlameUnavailable,
    GitHubSource,
    InvalidRepoUrl,
    RateLimited,
    RefNotFound,
    RepoNotFound,
    parse_repo_url,
)

API = "https://api.github.com"
SHA = "a" * 40


@pytest.fixture(autouse=True)
def _clear_cache():
    gh.IMMUTABLE_CACHE.clear()
    gh._REF_CACHE.clear()
    yield


@pytest.mark.parametrize(
    "url,owner,name,ref",
    [
        ("https://github.com/acme/shop", "acme", "shop", None),
        ("https://github.com/acme/shop.git", "acme", "shop", None),
        ("github.com/acme/shop/", "acme", "shop", None),
        ("https://github.com/acme/shop/tree/feature/x", "acme", "shop", "feature/x"),
        ("git@github.com:acme/shop.git", "acme", "shop", None),
        ("acme/shop", "acme", "shop", None),
    ],
)
def test_parse_repo_url(url, owner, name, ref):
    r = parse_repo_url(url)
    assert (r.owner, r.name, r.ref) == (owner, name, ref)


@pytest.mark.parametrize("url", ["https://gitlab.com/a/b", "not a url", "https://github.com/onlyowner"])
def test_parse_repo_url_invalid(url):
    with pytest.raises(InvalidRepoUrl):
        parse_repo_url(url)


def test_local_path_requires_flag(demo_repo):
    with pytest.raises(InvalidRepoUrl):
        parse_repo_url(str(demo_repo))
    assert parse_repo_url(str(demo_repo), allow_local=True).local_path


@respx.mock
async def test_resolve_default_branch_tree_and_file():
    respx.get(f"{API}/repos/acme/shop").respond(json={"default_branch": "main"})
    respx.get(f"{API}/repos/acme/shop/commits/main").respond(json={"sha": SHA})
    tree_route = respx.get(f"{API}/repos/acme/shop/git/trees/{SHA}").respond(
        json={"tree": [{"path": "app", "type": "tree"}, {"path": "app/user.py", "type": "blob"}], "truncated": False}
    )
    file_route = respx.get(f"{API}/repos/acme/shop/contents/app/user.py").respond(text="line1\nline2\n")

    src = GitHubSource(parse_repo_url("acme/shop"), token="t")
    assert await src.resolve_ref(None) == ("main", SHA)
    assert await src.get_tree(SHA) == (["app/user.py"], False)
    assert await src.get_file(SHA, "app/user.py") == "line1\nline2\n"
    # SHA-keyed cache: the second call does not hit the network
    await src.get_tree(SHA)
    await src.get_file(SHA, "app/user.py")
    assert tree_route.call_count == 1 and file_route.call_count == 1
    req = file_route.calls[0].request
    assert req.headers["Accept"] == "application/vnd.github.raw+json"
    assert req.headers["Authorization"] == "Bearer t"
    assert req.url.params["ref"] == SHA
    await src.aclose()


@respx.mock
async def test_repo_not_found_vs_ref_not_found():
    respx.get(f"{API}/repos/acme/missing").respond(404, json={"message": "Not Found"})
    src = GitHubSource(parse_repo_url("acme/missing"))
    with pytest.raises(RepoNotFound):
        await src.resolve_ref(None)

    respx.get(f"{API}/repos/acme/shop/commits/nope").respond(404, json={"message": "Not Found"})
    respx.get(f"{API}/repos/acme/shop").respond(json={"default_branch": "main"})
    src2 = GitHubSource(parse_repo_url("acme/shop"))
    with pytest.raises(RefNotFound):
        await src2.resolve_ref("nope")


@respx.mock
async def test_rate_limit_message():
    respx.get(f"{API}/repos/acme/shop").respond(
        403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"}, json={"message": "API rate limit exceeded"}
    )
    src = GitHubSource(parse_repo_url("acme/shop"))
    with pytest.raises(RateLimited) as exc:
        await src.resolve_ref(None)
    assert "GITHUB_TOKEN" in str(exc.value)


@respx.mock
async def test_blame_graphql():
    route = respx.post(f"{API}/graphql").respond(
        json={
            "data": {
                "repository": {
                    "object": {
                        "blame": {
                            "ranges": [
                                {
                                    "startingLine": 1,
                                    "endingLine": 10,
                                    "commit": {
                                        "oid": "b" * 40,
                                        "message": "init",
                                        "committedDate": "2026-09-01T10:00:00Z",
                                        "url": "https://github.com/acme/shop/commit/bbb",
                                        "author": {"name": "Deniz"},
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }
    )
    src = GitHubSource(parse_repo_url("acme/shop"), token="t")
    ranges = await src.blame(SHA, "app/user.py")
    assert ranges[0].start == 1 and ranges[0].end == 10 and ranges[0].commit.author == "Deniz"
    body = route.calls[0].request.content.decode()
    assert '"path":"app/user.py"' in body.replace(" ", "")


async def test_blame_requires_token():
    src = GitHubSource(parse_repo_url("acme/shop"), token="")
    with pytest.raises(BlameUnavailable):
        await src.blame(SHA, "x.py")


@respx.mock
async def test_get_commit_and_list():
    commit_json = {
        "sha": "c" * 40,
        "html_url": "https://github.com/acme/shop/commit/ccc",
        "parents": [{"sha": "d" * 40}],
        "commit": {
            "message": "fix things",
            "author": {"name": "Ece", "date": "2026-09-20T10:00:00Z"},
            "committer": {"name": "Ece", "date": "2026-09-20T10:00:00Z"},
        },
        "files": [{"filename": "app/user.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"}],
    }
    respx.get(f"{API}/repos/acme/shop/commits/{'c' * 40}").respond(json=commit_json)
    respx.get(f"{API}/repos/acme/shop/commits").respond(json=[commit_json])
    src = GitHubSource(parse_repo_url("acme/shop"), token="t")
    d = await src.get_commit("c" * 40)
    assert d.info.author == "Ece" and d.files[0].patch.startswith("@@")
    lst = await src.list_commits(SHA, 5)
    assert lst[0].message == "fix things"


@respx.mock
async def test_timeout_is_source_error():
    respx.get(f"{API}/repos/acme/shop").mock(side_effect=httpx.ConnectTimeout("t"))
    src = GitHubSource(parse_repo_url("acme/shop"))
    with pytest.raises(gh.SourceError):
        await src.resolve_ref(None)
