from typing import Dict, List, Optional

class FilingSchema:
    """Defines the structure and key items for a specific SEC filing type."""
    def __init__(self, name: str, mapping: Dict[str, str], default_items: List[str]):
        self.name = name
        self.mapping = mapping
        self.default_items = default_items

# --- 1. Form 10-K (Annual Report) ---
# Source: SEC Regulation S-K
SCHEMA_10K = FilingSchema(
    name="10-K",
    mapping={
        "Item 1": "Business",
        "Item 1A": "Risk Factors",
        "Item 1B": "Unresolved Staff Comments",
        "Item 1C": "Cybersecurity",  # New rule
        "Item 2": "Properties",
        "Item 3": "Legal Proceedings",
        "Item 5": "Market for Common Equity",
        "Item 7": "MD&A",  # Management’s Discussion and Analysis
        "Item 7A": "Quantitative Disclosures",
        "Item 8": "Financial Statements",
        "Item 9A": "Controls and Procedures",
        "Item 9B": "Other Information",
    },
    default_items=["Item 1", "Item 1A", "Item 7", "Item 7A", "Item 8"]
)

# --- 2. Form 10-Q (Quarterly Report) ---
# Note: Item numbers differ significantly from 10-K
SCHEMA_10Q = FilingSchema(
    name="10-Q",
    mapping={
        # Part I - Financial Information
        "Item 1": "Financial Statements",
        "Item 2": "MD&A",  # Management’s Discussion and Analysis
        "Item 3": "Market Risk",
        "Item 4": "Controls and Procedures",
        
        # Part II - Other Information
        "Item 1A": "Risk Factors",  # Updates to 10-K risks
        "Item 1": "Legal Proceedings", # Warning: Duplicate Item number in Part II
        "Item 2": "Unregistered Sales of Equity Securities",
        "Item 6": "Exhibits",
    },
    default_items=["Item 2", "Item 1", "Item 1A", "Item 3"]
)

# --- 3. Form 8-K (Current Report) ---
# Event-driven items
SCHEMA_8K = FilingSchema(
    name="8-K",
    mapping={
        # Section 1: Business and Operations
        "Item 1.01": "Material Agreements",
        "Item 1.02": "Termination of Agreements",
        "Item 1.05": "Cybersecurity Incidents",
        
        # Section 2: Financial Information
        "Item 2.01": "Acquisition/Disposition of Assets",
        "Item 2.02": "Results of Operations (Earnings)",
        "Item 2.03": "Financial Obligations",
        "Item 2.04": "Off-Balance Sheet Arrangements",
        
        # Section 3: Securities
        "Item 3.01": "Delisting Notice",
        "Item 3.02": "Unregistered Sales",
        
        # Section 4: Accountants
        "Item 4.01": "Changes in Certifying Accountant",
        "Item 4.02": "Non-Reliance on Financials",
        
        # Section 5: Governance
        "Item 5.01": "Changes in Control",
        "Item 5.02": "Departure of Directors/Officers",
        "Item 5.03": "Amendments to Articles",
        "Item 5.07": "Submission of Matters to a Vote",
        
        # Section 7: Regulation FD
        "Item 7.01": "Regulation FD Disclosure",
        
        # Section 8: Other Events
        "Item 8.01": "Other Events",
        
        # Section 9: Financial Statements
        "Item 9.01": "Financial Statements and Exhibits",
    },
    default_items=["Item 2.02", "Item 1.01", "Item 1.05", "Item 8.01", "Item 5.02"]
)

# --- 4. Form 20-F (Foreign Private Issuers Annual Report) ---
SCHEMA_20F = FilingSchema(
    name="20-F",
    mapping={
        "Item 3": "Key Information",
        "Item 3.D": "Risk Factors",
        "Item 4": "Information on the Company",
        "Item 5": "Operating and Financial Review (MD&A)",
        "Item 6": "Directors, Senior Management and Employees",
        "Item 10": "Additional Information",
        "Item 15": "Controls and Procedures",
        "Item 18": "Financial Statements",
    },
    default_items=["Item 3.D", "Item 4", "Item 5", "Item 18"]
)

# --- 5. Form 6-K (Foreign Private Issuers Current Report) ---
SCHEMA_6K = FilingSchema(
    name="6-K",
    mapping={
        # 6-K does not have standardized items like 8-K, usually just "Exhibits" or free text.
        # We map common headers if possible, otherwise generic.
        "Exhibits": "Exhibits",
    },
    default_items=[] # Usually process full text or specific exhibits
)


# Registry of schemas
_SCHEMAS = {
    "10-K": SCHEMA_10K,
    "10-Q": SCHEMA_10Q,
    "8-K": SCHEMA_8K,
    "20-F": SCHEMA_20F,
    "6-K": SCHEMA_6K,
}

def get_filing_schema(form_type: str) -> FilingSchema:
    """
    Get the schema definition for a given form type.
    Handles variations like '10-K/A', '10-Q/A'.
    Defaults to 10-K schema if unknown, or returns a generic empty schema.
    """
    if not form_type:
        return SCHEMA_10K
        
    # Normalize: "10-Q/A" -> "10-Q"
    base_form = form_type.upper().split('/')[0].strip()
    
    return _SCHEMAS.get(base_form, SCHEMA_10K)
