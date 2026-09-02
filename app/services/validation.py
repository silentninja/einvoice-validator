import io
import os
import re
from typing import List
from xml.etree import ElementTree as ET
from app.schemas import AuditSummary, ERPRemediation, PremiumAuditReport
from lxml import etree, isoschematron

VALID_UNECE_UNITS = {"PCE", "C62", "TO", "KGM", "BOX", "BG", "MTR", "HUR"}

ERP_REMEDIATION_MATRIX = {
    "sap": {
        "BR-CL-23": ERPRemediation(
            erp_module="SAP S/4HANA / ECC Sales & Distribution",
            target_table_or_view="Transaction CUNI / Table MARA-MEINS",
            cleanse_action_required="The internal SAP Unit of Measure code layout is missing global ISO/EDI mapping definitions. Navigate to transaction CUNI, locate your internal unit key, and explicitly map its external ISO code property to 'C62' (Pieces) or 'PCE' to ensure compliant XML tag output serialization.",
        ),
        "NETWORK-ROUTING": ERPRemediation(
            erp_module="SAP Business Partner Master Data",
            target_table_or_view="Table KNA1-STCD3 / STCEG",
            cleanse_action_required="The outbound Peppol routing pipeline is extracting a raw 15-digit TRN string without formatting prefixes. Update your custom ABAP data extraction program or SAP Document and Reporting Compliance (DRC) framework mapping views to automatically prepend the network routing identifier string '0235:' to the Tax Registration Number field.",
        ),
        "IBR-159-AE": ERPRemediation(
            erp_module="SAP FI-AR / Pricing Procedure Engine",
            target_table_or_view="Table KONV / Configuration Schema RVAA01",
            cleanse_action_required="When billing in foreign currency, the system fails to populate parallel tax calculations in local currency. Adjust the SD calculation schema to execute a parallel currency evaluation using the central bank's daily exchange rates, storing the computed tax values inside standard local currency arrays.",
        ),
    },
    "zoho": {
        "BR-CL-23": ERPRemediation(
            erp_module="Zoho Books Inventory Module",
            target_table_or_view="Items -> Unit Field Configurations",
            cleanse_action_required="Zoho Books outputs custom user-defined text labels into the item payload array. Modify your custom item catalog tables to replace plain-text descriptors like 'nos' or 'tons' with standard corporate tokens, or inject a custom Deluge script within the API integration webhooks to rewrite data parameters into standard codes before calling the ASP.",
        ),
        "NETWORK-ROUTING": ERPRemediation(
            erp_module="Zoho Books Customer Contacts",
            target_table_or_view="Customer Details -> Tax Registration Number",
            cleanse_action_required="The customer registration block maps raw numeric inputs directly to billing templates. Update the Customer Master fields to store the fully compliant routing prefix directly inside the contact profile string.",
        ),
        "IBR-159-AE": ERPRemediation(
            erp_module="Zoho Books Multi-Currency Transaction Engine",
            target_table_or_view="Invoice Currency & Localization Settings",
            cleanse_action_required="Enable the automated parallel exchange conversion field within Zoho Books settings or write a structural custom function to inject a parallel local currency tax summary block whenever an outbound invoice uses a non-AED currency designation.",
        ),
    },
    "netsuite": {
        "BR-CL-23": ERPRemediation(
            erp_module="Oracle NetSuite Supply Chain Architecture",
            target_table_or_view="Lists -> Supply Chain -> Units of Measure",
            cleanse_action_required="Update the global Units of Measure translation record table. Map the base internal abbreviation string to the strict alphanumeric tokens required by the Peppol network registry framework.",
        )
    },
}


def convert_normalized_payload_to_ubl_xml(data: dict) -> bytes:
    """
    Transforms uniform structured dictionary models into a valid, 
    in-memory UBL 2.1 standard XML Document byte stream.
    """
    is_cn = data.get("document_type") == "CREDIT_NOTE"
    root_tag = "CreditNote" if is_cn else "Invoice"
    
    namespaces = {
        "xmlns": f"urn:oasis:names:specification:ubl:schema:xsd:{root_tag}-2",
        "xmlns:cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
        "xmlns:cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
    }

    root = ET.Element(root_tag, namespaces)
    
    # Structural Headers
    cbc_id = ET.SubElement(root, "cbc:ID")
    cbc_id.text = str(data.get("invoice_number", "TEMP-ID"))
    
    # Corporate Identity Data Hub (TRN Target Coordinates)
    supplier_party = ET.SubElement(root, "cac:AccountingSupplierParty")
    party = ET.SubElement(supplier_party, "cac:Party")
    party_legal = ET.SubElement(party, "cac:PartyLegalEntity")
    company_id = ET.SubElement(party_legal, "cbc:CompanyID")
    company_id.text = str(data.get("supplier_trn", ""))
    
    # Item Row Array Intermediary Generation Loops
    line_tag = "cac:CreditNoteLine" if is_cn else "cac:InvoiceLine"
    qty_tag = "cbc:CreditedQuantity" if is_cn else "cbc:InvoicedQuantity"
    
    for idx, item in enumerate(data.get("invoice_lines", []), 1):
        line_node = ET.SubElement(root, line_tag)
        
        l_id = ET.SubElement(line_node, "cbc:ID")
        l_id.text = str(idx)
        
        qty_node = ET.SubElement(line_node, qty_tag, unitCode=str(item.get("unit", "")))
        qty_node.text = str(item.get("quantity", "0"))
        
        price_node = ET.SubElement(line_node, "cac:Price")
        price_amount = ET.SubElement(price_node, "cbc:PriceAmount")
        price_amount.text = str(item.get("price", "0.00"))
        
        ext_amount = ET.SubElement(line_node, "cbc:LineExtensionAmount")
        ext_amount.text = str(item.get("total", "0.00"))

    return ET.tostring(root, encoding="utf-8")

def validate_invoice_structure(instance_doc, filename, root) -> PremiumAuditReport:
    """
    Validates the structural baseline and XSD alignment of a UBL Document.
    Safely captures missing root fields and routes documents correctly.
    """
    detected_deficits = []
    doc_type_label = "UNKNOWN"
    COMMON_DIR = "/Users/mukesh/PycharmProjects/einvoice-validator/app/pint-schema/common"
    MAIN_XSD_PATH = os.path.join(COMMON_DIR, "UBL-Invoice-2.1.xsd")
    MAIN_CRED_XSD_PATH = os.path.join(COMMON_DIR, "UBL-CreditNote-2.1.xsd")
        # 2. Create a custom resolver to force lxml to look directly in your common folder
    class UBLResolver(etree.Resolver):
        def resolve(self, url, pubid, context):
            # Extract just the filename from the requested path (e.g., handles "../common/file.xsd")
            filename = os.path.basename(url)
            local_path = os.path.join(COMMON_DIR, filename)
            
            if os.path.exists(local_path):
                return self.resolve_filename(local_path, context)
            return None

# 3. Configure the parser with the resolver
    parser = etree.XMLParser()
    parser.resolvers.add(UBLResolver())

    # 4. Parse the main schema file using our custom configuration
            # 1. Parse the incoming file stream safely
        # Handles binary file objects from FastAPI/Flask or local file buffers
    bytes_stream = io.BytesIO(instance_doc)
    
    # 2. Peek at the root tag first using a streaming pull parser
    context = etree.iterparse(bytes_stream, events=("start",))
    _, root_element = next(context)
    root_tag = root_element.tag
    del context     
    # Determine human-readable label
    if "Invoice" in root_tag:
        doc_type_label = "UBL INVOICE"
    elif "CreditNote" in root_tag:
        doc_type_label = "UBL CREDIT NOTE"
    bytes_stream.seek(0)
    instance_doc = etree.parse(bytes_stream, parser=parser)
    root = instance_doc.getroot()
    # 2. Namespaces for explicit structural assertion
    namespaces = {
        'ubl': 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
        'cn': 'urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2',
        'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
    }

    # 3. Step 1: Explicit Structural Safety Check (Catches missing fields instantly)
    mandatory_fields = {
        'UBLVersionID': 'cbc:UBLVersionID',
        'CustomizationID': 'cbc:CustomizationID',
        'ProfileID': 'cbc:ProfileID',
        'ID': 'cbc:ID',
        'IssueDate': 'cbc:IssueDate',
        'DocumentCurrencyCode': 'cbc:DocumentCurrencyCode'
    }

    for field_name, xpath_query in mandatory_fields.items():
        # Use a relative './' path execution directly on the root element
        val = root.xpath(f'./{xpath_query}/text()', namespaces=namespaces)
        print(val)
        if not val or not val[0].strip():
            detected_deficits.append({
                "rule_id": f"STRUCT-MISSING-{field_name.upper()}",
                "network_message": f"Critical field '{field_name}' is completely missing or empty from the root layout.",
                "xpath_location": f"/{root_tag}/{xpath_query}",
                "detected_raw_value": "MISSING_OR_EMPTY",
                "document_line_context": "0",  # Passed as string
                "erp_remediation": None
            })

        # Step 2: Full XSD Schema Check (Only run if base layout is clean to avoid parser panic)
        if not detected_deficits:
            active_schema = None
            
            # Setup the parser with our file path resolver
            parser = etree.XMLParser()
            parser.resolvers.add(UBLResolver())

            if "Invoice" in root_tag:
                invoice_xsd_path = os.path.join(COMMON_DIR, "UBL-Invoice-2.1.xsd")
                invoice_xsd_doc = etree.parse(invoice_xsd_path, parser=parser)
                active_schema = etree.XMLSchema(invoice_xsd_doc)
            elif "CreditNote" in root_tag:
                creditnote_xsd_path = os.path.join(COMMON_DIR, "UBL-CreditNote-2.1.xsd")
                creditnote_xsd_doc = etree.parse(creditnote_xsd_path, parser=parser)
                active_schema = etree.XMLSchema(creditnote_xsd_doc)

            if active_schema is not None:
                if not active_schema.validate(instance_doc):
                    for error in active_schema.error_log:
                        detected_deficits.append({
                            "rule_id": "XSD-SYNTAX-FAIL",
                            "network_message": error.message,
                            "xpath_location": error.path if error.path else f"/{root_tag}",
                            "detected_raw_value": "INVALID_XSD_SYNTAX",
                            "document_line_context": str(error.line),  # Passed as string
                            "erp_remediation": None
                        })
            else:
                detected_deficits.append({
                    "rule_id": "XSD-ROOT-DECLARATION-FAIL",
                    "network_message": f"Unsupported UBL root element element or layout tags: '{root_tag}'.",
                    "xpath_location": f"/{root_tag}",
                    "detected_raw_value": root_tag,
                    "document_line_context": "1",  # Passed as string
                    "erp_remediation": None
                })



        # Return structured response mapping perfectly to your model schemas
    return detected_deficits