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


def _parse_git_url(url: str) -> tuple[str, str | None]:
    """Parse git URL into (src_path, vcs_ref).

    Strips git+ prefix and extracts @ref if present.
    Converts file:// URLs to local paths.
    """
    # Strip git+ prefix
    if url.startswith("git+"):
        url = url[4:]

    # Convert file:// URLs to local paths
    parsed = urlparse(url)
    if parsed.scheme == "file":
        url = Path(unquote(parsed.path)).resolve().as_posix()

    # Extract @ref
    if "@" in url and "://" in url:
        # Only split on @ if it comes after ://
        scheme_end = url.index("://") + 3
        path_part = url[scheme_end:]
        if "@" in path_part:
            base_url = url[:scheme_end] + path_part.split("@")[0]
            vcs_ref = path_part.split("@", 1)[1]
            return base_url, vcs_ref

    return url, None


def _repo_from_direct_url(data: dict[str, Any]) -> tuple[str, str | None] | None:
    """Extract repository source and vcs_ref from direct_url.json."""
    url = data.get("url")
    if not isinstance(url, str):
        return None

    src_path, vcs_ref = _parse_git_url(url)

    # Override with vcs_info if available
    vcs_info = data.get("vcs_info")
    if isinstance(vcs_info, dict):
        # requested_revision is what user specified, commit_id is what was resolved
        revision = vcs_info.get("requested_revision") or vcs_info.get("commit_id")
        if revision:
            vcs_ref = revision

    return src_path, vcs_ref


def _repo_from_git(path: Path) -> str | None:
    try:
        repo = Repo(path, search_parent_directories=True)
    except (InvalidGitRepositoryError, NoSuchPathError):
        return None
    return repo.working_tree_dir


def resolve_framework_repo(
    source_override: str | None = None,
) -> tuple[str, str | None, list[str]]:
    """Resolve repository source and vcs_ref with trace information.

    Returns:
        (src_path, vcs_ref, trace) where:
        - src_path: Repository URL or local path
        - vcs_ref: Git reference (branch/tag/commit) or None
        - trace: List of resolution steps
    """
    trace: list[str] = []

    if source_override:
        src_path, vcs_ref = _parse_git_url(source_override)
        trace.append(f"--from override provided: {src_path} (ref={vcs_ref})")
        return src_path, vcs_ref, trace

    env_repo = os.getenv("FRAMEWORK_REPO")
    if env_repo:
        src_path, vcs_ref = _parse_git_url(env_repo)
        trace.append(f"FRAMEWORK_REPO environment set: {src_path} (ref={vcs_ref})")
        return src_path, vcs_ref, trace
    trace.append("FRAMEWORK_REPO environment not set")

    direct_url = _load_direct_url()
    if direct_url:
        result = _repo_from_direct_url(direct_url)
        if result:
            src_path, vcs_ref = result
            trace.append(
                f"direct_url.json found: url={direct_url.get('url')} "
                f"resolved=({src_path}, ref={vcs_ref})"
            )
            return src_path, vcs_ref, trace
    else:
        trace.append("direct_url.json not found")

    git_repo = _repo_from_git(Path(__file__).resolve())
    if git_repo:
        trace.append(f"Local git repository detected: {git_repo}")
        return git_repo, None, trace
    trace.append("Local git repository not detected")

    trace.append(f"Falling back to default repository: {DEFAULT_REPO}")
    return DEFAULT_REPO, None, trace


def detect_framework_repo() -> tuple[str, str | None]:
    """Detect framework repository source and vcs_ref.

    Returns:
        (src_path, vcs_ref) tuple
    """
    src_path, vcs_ref, trace = resolve_framework_repo()
    for line in trace:
        _debug(line)
    _debug(f"Framework templates source resolved to: {src_path} (ref={vcs_ref})")
    return src_path, vcs_ref


REPO, VCS_REF = detect_framework_repo()


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
        vcs_ref=VCS_REF,
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
            vcs_ref=VCS_REF,
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
    src_path, vcs_ref, trace = resolve_framework_repo(from_)
    print("Framework repository resolution:")
    for line in trace:
        print(f"- {line}")
    print(f"=> Selected source: {src_path}")
    print(f"=> VCS reference: {vcs_ref or 'HEAD (default branch)'}")


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
