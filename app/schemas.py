from pydantic import BaseModel, Field
from typing import List

# Locked model names and field configurations
class ERPRemediation(BaseModel):
    erp_module: str
    target_table_or_view: str
    cleanse_action_required: str

class DetailedDeficit(BaseModel):
    rule_id: str
    severity: str = "CRITICAL_BLOCKING"
    network_message: str
    xpath_location: str
    detected_raw_value: str
    document_line_context: str
    erp_remediation: ERPRemediation | None = None

class AuditSummary(BaseModel):
    total_deficits_found: int
    document_type: str

class PremiumAuditReport(BaseModel):
    file_name: str
    assessment_status: str
    summary: AuditSummary
    errors: List[DetailedDeficit]

# Internal schemas strictly for structuring Gemini's PDF text extraction outputs
class ExtractedInvoiceLine(BaseModel):
    line_id: int = Field(description="Sequential sequence line index number starting at 1.")
    unit: str = Field(description="The exact text or token code layout for the unit of measure, e.g., 'nos', 'H87', 'PCE'.")
    quantity: float = Field(description="Numeric volume quantity sold.")
    price: float = Field(description="Net price per single item unit.")
    total: float = Field(description="Line extension total calculation amount.")

class ExtractedInvoiceSchema(BaseModel):
    document_type: str = Field(description="Must be exactly 'INVOICE' or 'CREDIT_NOTE'.")
    invoice_number: str = Field(description="The unique identifier reference token of the document.")
    supplier_trn: str = Field(description="The 15-digit Tax Registration Number of the selling corporation.")
    invoice_lines: List[ExtractedInvoiceLine] = Field(description="Array list of all item rows.")