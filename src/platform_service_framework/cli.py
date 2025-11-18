import json
import os
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import unquote, urlparse

from copier import run_copy, run_update
from cyclopts import App, Parameter
from git import Repo
from git.exc import InvalidGitRepositoryError, NoSuchPathError
from importlib import metadata

app = App(
    name="platform-service-framework",
    help="Framework for building Django applications",
)

DEFAULT_REPO = "https://github.com/ansible/platform-service-framework"
DEBUG = os.getenv("FRAMEWORK_DEBUG")


def _debug(message: str) -> None:
    if DEBUG:
        print(f"[framework/debug] {message}")


def _load_direct_url() -> dict[str, Any] | None:
    """Return direct_url.json data when available (PEP 610)."""
    try:
        dist = metadata.distribution("platform-service-framework")
    except metadata.PackageNotFoundError:
        return None

    files = dist.files or []
    direct_url_file = next((f for f in files if f.name == "direct_url.json"), None)
    if not direct_url_file:
        return None

    direct_url_path = dist.locate_file(direct_url_file)
    try:
        with direct_url_path.open() as fp:
            return json.load(fp)
    except (OSError, json.JSONDecodeError):
        return None


def _normalize_repo_url(url: str) -> str:
    """Convert file:// URLs to local paths for copier compatibility."""
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).resolve().as_posix()
    return url


def _repo_from_direct_url(data: dict[str, Any]) -> str | None:
    url = data.get("url")
    if not isinstance(url, str):
        return None

    repo_url = _normalize_repo_url(url)
    vcs_info = data.get("vcs_info")
    if isinstance(vcs_info, dict):
        revision = vcs_info.get("requested_revision") or vcs_info.get("commit_id")
        if revision and "@" not in repo_url:
            return f"{repo_url}@{revision}"
    return repo_url


def _repo_from_git(path: Path) -> str | None:
    try:
        repo = Repo(path, search_parent_directories=True)
    except (InvalidGitRepositoryError, NoSuchPathError):
        return None
    return repo.working_tree_dir


def resolve_framework_repo(source_override: str | None = None) -> tuple[str, list[str]]:
    """Resolve repository/branch with trace information."""
    trace: list[str] = []

    if source_override:
        repo = _normalize_repo_url(source_override)
        trace.append(f"--from override provided: {repo}")
        return repo, trace

    env_repo = os.getenv("FRAMEWORK_REPO")
    if env_repo:
        repo = _normalize_repo_url(env_repo)
        trace.append(f"FRAMEWORK_REPO environment set: {repo}")
        return repo, trace
    trace.append("FRAMEWORK_REPO environment not set")

    direct_url = _load_direct_url()
    if direct_url:
        repo_url = _repo_from_direct_url(direct_url)
        vcs_info = direct_url.get("vcs_info") if isinstance(direct_url, dict) else None
        revision = None
        if isinstance(vcs_info, dict):
            revision = vcs_info.get("requested_revision") or vcs_info.get("commit_id")
        trace.append(
            f"direct_url.json found: url={direct_url.get('url')} revision={revision} "
            f"resolved={repo_url}"
        )
        if repo_url:
            return repo_url, trace
    else:
        trace.append("direct_url.json not found")

    git_repo = _repo_from_git(Path(__file__).resolve())
    if git_repo:
        trace.append(f"Local git repository detected: {git_repo}")
        return git_repo, trace
    trace.append("Local git repository not detected")

    trace.append(f"Falling back to default repository: {DEFAULT_REPO}")
    return DEFAULT_REPO, trace


def detect_framework_repo() -> str:
    repo, trace = resolve_framework_repo()
    for line in trace:
        _debug(line)
    _debug(f"Framework templates source resolved to: {repo}")
    return repo


REPO = detect_framework_repo()


@app.command
def init(
    destination: Path | None = None,
    project: Annotated[str | None, Parameter(alias="-p")] = None,
    apps: Annotated[list[str], Parameter(consume_multiple=True)] = ["api"],
):
    """Initialize a new Django Project.

    ## Examples
    ```bash
    # New project on current folder with one app named api:
    platform-service-framework init
    # New project on specific folder with one app named api:
    platform-service-framework init /tmp/foo
    # New project on named folder with 3 apps:
    platform-service-framework init my-service --apps api web core

    ```
    ---
    Args:
        destination: The root of the repository
        project: project name [default to destination folder name]
        apps: names for each app to be initialized
    """
    destination = destination or Path.cwd()
    project = project or destination.name.replace("-", "_")
    if not destination.exists():
        destination.mkdir(parents=True, exist_ok=True)
    if not Path(destination / ".git").exists():
        Repo.init(str(destination))

    print(f"Initializing your project on {destination}")
    run_copy(
        REPO,
        destination,
        data={
            "project_name": project,
            "template": "templates/project",
        },
    )
    print("Main project created.")

    apps_destination = destination / "apps"
    for app_name in apps:
        run_copy(
            REPO,
            apps_destination / app_name,
            data={
                "template": "templates/app",
            },
        )
        print(f"Created app {app_name}")

    if apps:
        # Ensure apps is a Python module so each app can be imported
        Path(apps_destination / "__init__.py").touch()

    print("…" * 40)
    print("Framework init finished")
    print(f"Created project at {destination}/{project}")
    if apps:
        print(f"Created apps at {destination}/apps/[{','.join(apps)}]")


@app.command
def debug(
    from_: Annotated[str | None, Parameter(name="--from")] = None,
):
    """Show how the framework source is resolved."""
    repo, trace = resolve_framework_repo(from_)
    print("Framework repository resolution:")
    for line in trace:
        print(f"- {line}")
    print(f"=> Selected source: {repo}")


@app.command
def update(destination: Path | None = None):
    """Update an existing application"""
    destination = destination or Path.cwd()
    print(f"Updating your app on {destination}")
    run_update(
        destination,
        overwrite=True,
        skip_answered=True,
    )


@app.command
def validate(destination: Path | None = None):
    """Validate an existing application"""
    destination = destination or Path.cwd()

    print(f"Validating your app on {destination}")


@app.command
def completions():
    """generate shell completions."""
    print(app.generate_completion())
