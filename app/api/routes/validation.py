import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import List
from google import genai
from lxml import etree, isoschematron
from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, types, status
from saxonche import PySaxonProcessor
from app.schemas import AuditSummary, DetailedDeficit, ERPRemediation, ExtractedInvoiceSchema, PremiumAuditReport
from app.services.validation import ERP_REMEDIATION_MATRIX, convert_normalized_payload_to_ubl_xml, validate_invoice_structure

router = APIRouter()

ai_client = None

# ─── COMPILATION: NATIVE XSLT COMPLIANCE LAYER ───
CURRENT_FILE_DIR = Path(__file__).resolve().parent.parent.parent

# 2. Build a stable absolute path pointing directly into the schemas subfolder
# This resolves to: /your_project_root/app/schemas/PINT-jurisdiction-aligned-rules.xslt
XSLT_INV_PATH = CURRENT_FILE_DIR / "pint-schema" / "PINT-ae-ubl.xslt"
XSLT_CORE_PATH = CURRENT_FILE_DIR / "pint-schema" / "PINT-ae.xslt"
XSLT_UBL_PATH = CURRENT_FILE_DIR / "pint-schema" / "PINT-core.xslt"
XSD_PATH = CURRENT_FILE_DIR / "pint-schema" / "maindoc" / "ubl.xsd"


saxon_processor = PySaxonProcessor(license=False)

xslt_compiler = saxon_processor.new_xslt30_processor()

validation_stages = [
        # Stage 1: Global PINT Core Foundation Layer
        (xslt_compiler.compile_stylesheet(stylesheet_file=str(XSLT_CORE_PATH)), "[Core Framework] "),       
        
        # Stage 2A: Regional UAE PINT-AE Syntax/UBL Layer
        (xslt_compiler.compile_stylesheet(stylesheet_file=str(XSLT_UBL_PATH)), "[UAE Syntax Mapping] "), 
        
        # Stage 2B: Regional UAE PINT-AE Business & Tax Logic Layer
        (xslt_compiler.compile_stylesheet(stylesheet_file=str(XSLT_INV_PATH)), "[UAE Local Tax] ")     
    ]

# ─── 2. RESPONSE PYDANTIC SCHEMAS ───

@router.post("/api/v1/validate", response_model=PremiumAuditReport, summary="Validate an invoice or credit note payload against UAE PINT-AE compliance rules.")
async def validate_transaction_payload(
    response: Response,
    file: UploadFile = File(...),
    erp_type: str = Query("sap", description="Specify target system: 'sap', 'zoho', or 'netsuite' to fetch tailored database remediation instructions."),
):

    contents = await file.read()
    file_extension = file.filename.split(".")[-1].lower()
    
    invoice_xml_tree = None
    doc_type_label = "INVOICE"

    # Step 1: Execute Dynamic Multi-Format Ingestion Transformations
    if file_extension == "xml":
        try:
            invoice_xml_tree = etree.fromstring(contents)
            doc_type_label = "CREDIT_NOTE" if "CreditNote" in invoice_xml_tree.tag else "INVOICE"
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Malformed XML syntax construction footprint: {str(e)}")

    elif file_extension == "json":
        try:
            raw_json = json.loads(contents.decode("utf-8"))
            doc_type_label = "CREDIT_NOTE" if raw_json.get("document_type") == "CREDIT_NOTE" else "INVOICE"
            xml_bytes = convert_normalized_payload_to_ubl_xml(raw_json)
            invoice_xml_tree = etree.fromstring(xml_bytes)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Malformed JSON properties structure context: {str(e)}")

    elif file_extension == "pdf":
        try:
            # Deterministic, structure-enforced visual extraction routing via Gemini 2.5 Flash
            ai_response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[
                    types.Part.from_bytes(data=contents, mime_type='application/pdf'),
                    "Extract all target billing structural records, reference tracking indices, partner registration numbers, and matrix line items from this asset canvas."
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ExtractedInvoiceSchema,
                    temperature=0.0  # Force zero variance output patterns
                ),
            )
            extracted_data = json.loads(ai_response.text)
            doc_type_label = extracted_data.get("document_type", "INVOICE")
            
            xml_bytes = convert_normalized_payload_to_ubl_xml(extracted_data)
            invoice_xml_tree = etree.fromstring(xml_bytes)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Gemini multimodal extraction boundary failure: {str(e)}")
    else:
        raise HTTPException(status_code=400, detail="Unsupported upload asset serialization layout extension.")
        
        # 3. Dynamically fetch the matching target validator ruleset
    # Use lxml EXCLUSIVELY for routing detection without mutating the content 
    # Select your validator from the pre-compiled v1.0.4 cache registry

    # 🚨 FIX: Pass the raw bytes payload directly to the Saxon processor 
    # to avoid string encoding errors or carriage return injections
# Ensure namespaces are ready for processing
    svrl_ns = {"svrl": "http://purl.oclc.org/dsdl/svrl"}
    detected_deficits = []
    xml_text_payload = contents.decode("utf-8")

    # =========================================================================
    # STAGE 0: BASE UBL 2.1 LXML SCHEMA VALIDATION (Structural Fast-Fail Gate)
    # =========================================================================
    detected_deficits.extend(validate_invoice_structure(contents, file.filename, doc_type_label))

    # =========================================================================
    # PREPARE XDM NODE FOR MULTI-STAGE SCHEMATRON VALIDATION
    # =========================================================================
    native_xdm_node = saxon_processor.parse_xml(xml_text=xml_text_payload)


    # =========================================================================
    # EXECUTE SEQUENTIAL TRANSFORMATION PIPELINE
    # =========================================================================
    for validator_engine, layer_prefix in validation_stages:
        validator_engine.clear_parameters()
        svrl_report_string = validator_engine.transform_to_string(xdm_node=native_xdm_node)
        svrl_report_tree = etree.fromstring(svrl_report_string.encode("utf-8"))
        failed_assertions = svrl_report_tree.xpath("//svrl:failed-assert", namespaces=svrl_ns)

        for assertion in failed_assertions:
            rule_id = assertion.get("id", "UNKNOWN-RULE")
            xpath_loc = assertion.get("location", "/")
            
            text_node = assertion.find("svrl:text", namespaces=svrl_ns)
            network_msg = f"{layer_prefix}{text_node.text.strip()}" if text_node is not None else f"{layer_prefix}Compliance rule exception raised."
            
            # Scraping Engine to Pinpoint Dirty Entities
            detected_val = "NOT_FOUND"
            friendly_line_context = "Header Level"
            
            if xpath_loc != "/":
                try:
                    cleaned_xpath = xpath_loc.replace("/*", "//*").replace("[1]", "")
                    target_elements = invoice_xml_tree.xpath(cleaned_xpath, namespaces=invoice_xml_tree.nsmap)
                    if target_elements:
                        detected_val = target_elements[0].text or target_elements[0].attrib.get("unitCode", "VAL_ATTR")
                    
                    line_index_match = re.search(r'Line\[(\d+)\]', xpath_loc)
                    if line_index_match:
                        friendly_line_context = f"Document Line Array Position: {int(line_index_match.group(1)) - 1}"
                except Exception:
                    pass

            # Resolve Local Master Knowledge Base References
            selected_erp = erp_type.lower()
            system_map = ERP_REMEDIATION_MATRIX.get(selected_erp, ERP_REMEDIATION_MATRIX["sap"])
            rule_remedy = system_map.get(rule_id, system_map.get(rule_id.upper(), ERPRemediation(
                erp_module=f"{selected_erp.upper()} Financial Management Core",
                target_table_or_view="Global Table Parameter Registries",
                cleanse_action_required=f"Regulatory compliance deficit raised for Rule ID: {rule_id}. Cleanse target data fields to align with baseline network constraints."
            )))

            detected_deficits.append(DetailedDeficit(
                rule_id=rule_id,
                network_message=network_msg,
                xpath_location=xpath_loc,
                detected_raw_value=str(detected_val),
                document_line_context=friendly_line_context,
                erp_remediation=rule_remedy 
            ))

    # Final Summary Compilation Output
    if len(detected_deficits) == 0:
        assessment_status = "COMPLIANT"
    else:
        assessment_status = "STRUCTURAL DEFICIT DETECTED"
        response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY

    return PremiumAuditReport(
        file_name=file.filename,
        assessment_status=assessment_status,
        summary=AuditSummary(
            total_deficits_found=len(detected_deficits),
            document_type=doc_type_label
        ),
        errors=detected_deficits
    )