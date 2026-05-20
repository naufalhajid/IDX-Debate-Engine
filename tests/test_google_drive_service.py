import pytest

from services import google_drive_service as gds


def test_google_drive_service_rejects_missing_service_account(monkeypatch):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT", raising=False)

    with pytest.raises(ValueError, match="GOOGLE_SERVICE_ACCOUNT is empty"):
        gds.GoogleDriveService()


def test_google_drive_service_rejects_invalid_service_account_json(monkeypatch):
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT", "{not-json")

    with pytest.raises(ValueError, match="GOOGLE_SERVICE_ACCOUNT is not valid JSON"):
        gds.GoogleDriveService()


def test_google_drive_service_initializes_with_valid_service_account(
    monkeypatch,
):
    service_account_info = '{"type":"service_account","project_id":"test-project"}'
    creds = object()
    build_calls = []

    def fake_from_service_account_info(info):
        assert info == {"type": "service_account", "project_id": "test-project"}
        return creds

    def fake_build(api_name, api_version, *, credentials):
        build_calls.append((api_name, api_version, credentials))
        return f"{api_name}-{api_version}-service"

    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT", service_account_info)
    monkeypatch.setattr(
        gds.service_account.Credentials,
        "from_service_account_info",
        fake_from_service_account_info,
    )
    monkeypatch.setattr(gds, "build", fake_build)

    service = gds.GoogleDriveService()

    assert service.creds is creds
    assert service.sheet_service == "sheets-v4-service"
    assert service.drive_service == "drive-v3-service"
    assert build_calls == [
        ("sheets", "v4", creds),
        ("drive", "v3", creds),
    ]
