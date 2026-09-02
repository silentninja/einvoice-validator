import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Import your FastAPI app creation instance here
from app.main import app  

client = TestClient(app)

# Dynamically locate your testing folder matching your project layout
TESTS_ROOT_DIR = Path(__file__).parent / "pint-ae"
# Recursively collect all XML files (.rglob looks inside all subfolders like inv/ and cn/)
xml_test_files = list(TESTS_ROOT_DIR.rglob("*.xml"))

@pytest.mark.parametrize("file_path", xml_test_files, ids=lambda p: str(p.relative_to(TESTS_ROOT_DIR.parent)))
def test_batch_xml_compliance_matrix(file_path: Path):
    """
    In-memory validation execution across the entire localized XML dataset.
    """
    assert file_path.exists(), f"Target test asset missing: {file_path}"

    # Open and stream the file payload directly into the TestClient
    with open(file_path, "rb") as f:
        response = client.post(
            "/api/v1/validate",
            files={"file": (file_path.name, f, "text/xml")},
            params={"erp_type": "sap"},
        )
    
    # Assert that the endpoint handled the payload successfully (HTTP 200)
    assert response.status_code == 200 or response.status_code == 422, f"Server routing failed for {file_path.name}: {response.text}"
    
    report = response.json()
    
    # Optional Assertion: If you want to see which files are failing/passing inside pytest
    # print statements will show up in the terminal if you run pytest with the '-s' flag
    print(f"\n[AUDIT] {file_path.name} -> Status: {report['assessment_status']}")
    
    # If a file is structurally defective, we can inspect its error array output
    if report["assessment_status"] != "COMPLIANT":
        for deficit in report.get("errors", []):
            print(f"   Rule Triggered: {deficit['rule_id']} - {deficit['network_message']}")
            print(deficit['erp_remediation'])

    # You can assert True here just to ensure the validation cycle completed without exceptions,
    # or assert report["assessment_status"] == "COMPLIANT" if you expect all these test files to be clean.
    assert "assessment_status" in report