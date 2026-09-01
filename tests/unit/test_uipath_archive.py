from pathlib import Path
from zipfile import ZipFile

PROJECT_ROOT = Path(__file__).parents[2]
ARCHIVE_PATH = PROJECT_ROOT / "uipath/InvoiceOps-RPA-Demo.uis"
FORBIDDEN_PATH_SEGMENTS = ("/.local/", "/.storage/", "/.screenshots/")
REQUIRED_MEMBERS = frozenset(
    {
        "SolutionStorage.json",
        "Solution.uipx",
        "InvoiceOps-RPA-Demo/Main.xaml",
        "InvoiceOps-RPA-Demo/project.json",
        "InvoiceOps-RPA-Demo/project.uiproj",
        "InvoiceOps-RPA-Demo/entry-points.json",
        "InvoiceOps-RPA-Demo/.project/JitCustomTypes.json",
        "InvoiceOps-RPA-Demo/.project/PackagerFlags.json",
        "resources/solution_folder/package/InvoiceOps-RPA-Demo.json",
        "resources/solution_folder/process/process/InvoiceOps-RPA-Demo.json",
    }
)


def test_uipath_distributable_excludes_studio_state_and_keeps_project_members() -> None:
    with ZipFile(ARCHIVE_PATH) as archive:
        members = frozenset(archive.namelist())
        assert archive.testzip() is None

    assert REQUIRED_MEMBERS <= members
    assert not any(segment in member for segment in FORBIDDEN_PATH_SEGMENTS for member in members)
