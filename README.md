# einvoice-validator

A structured FastAPI service for validating invoice payloads against a deterministic compliance ruleset.

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## API

- POST /api/v1/validate
  - Upload a JSON, XML, or PDF file
  - Optionally pass `erp_type=sap|zoho|netsuite`
