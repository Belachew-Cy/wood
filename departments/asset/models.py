from datetime import datetime, date

from extensions import db


# ============================================================================
# ASSET CATEGORIES
# ============================================================================

class AssetCategory(db.Model):
    __tablename__ = "asset_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    code = db.Column(db.String(50), unique=True)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default="Active")

    parent_id = db.Column(
        db.Integer,
        db.ForeignKey("asset_categories.id"),
        nullable=True,
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    parent = db.relationship(
        "AssetCategory",
        remote_side="AssetCategory.id",
        backref="subcategories",
    )

    assets = db.relationship("Asset", back_populates="category")


# ============================================================================
# ASSET
# ============================================================================

class Asset(db.Model):
    __tablename__ = "assets"

    id = db.Column(db.Integer, primary_key=True)
    asset_code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)

    category_id = db.Column(
        db.Integer,
        db.ForeignKey("asset_categories.id"),
        nullable=False,
    )

    serial_number = db.Column(db.String(100))
    model_number = db.Column(db.String(100))
    manufacturer = db.Column(db.String(120))

    purchase_date = db.Column(db.Date)
    purchase_cost = db.Column(db.Numeric(15, 2), default=0)
    warranty_expiry = db.Column(db.Date)

    supplier_id = db.Column(db.Integer, nullable=True)
    purchase_order_number = db.Column(db.String(100))
    funding_source = db.Column(db.String(100))

    department_id = db.Column(db.Integer, nullable=True)
    employee_id = db.Column(db.Integer, nullable=True)

    location = db.Column(db.String(200))

    condition = db.Column(db.String(30), default="Good")
    status = db.Column(db.String(30), default="Available")

    asset_image = db.Column(db.String(255))
    document_path = db.Column(db.String(255))

    # Depreciation
    useful_life_years = db.Column(db.Integer)
    salvage_value = db.Column(db.Numeric(15, 2), default=0)
    depreciation_method = db.Column(
        db.String(30), default="Straight Line", nullable=False
    )
    depreciation_start_date = db.Column(db.Date, nullable=True)
    depreciation_status = db.Column(
        db.String(20), default="Active", nullable=False
    )

    # Identification
    barcode = db.Column(
        db.String(100), unique=True, nullable=True, index=True
    )
    asset_type = db.Column(db.String(50), default="Fixed Asset")

    # Disposal
    disposal_status = db.Column(
        db.String(30), default="Active", nullable=False
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    category = db.relationship("AssetCategory", back_populates="assets")


# ============================================================================
# ASSET ASSIGNMENT
# ============================================================================

class AssetAssignment(db.Model):
    __tablename__ = "asset_assignments"

    id = db.Column(db.Integer, primary_key=True)

    asset_id = db.Column(
        db.Integer, db.ForeignKey("assets.id"), nullable=False
    )

    department_id = db.Column(db.Integer, nullable=False)
    employee_id = db.Column(db.Integer, nullable=False)

    assigned_date = db.Column(
        db.Date, nullable=False, default=date.today
    )
    returned_date = db.Column(db.Date)

    assigned_by = db.Column(db.Integer)
    remarks = db.Column(db.Text)

    status = db.Column(
        db.String(30), default="Active", nullable=False
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    asset = db.relationship("Asset", backref="assignment_history")


# ============================================================================
# ASSET TRANSFER
# ============================================================================

class AssetTransfer(db.Model):
    __tablename__ = "asset_transfers"

    id = db.Column(db.Integer, primary_key=True)

    asset_id = db.Column(
        db.Integer, db.ForeignKey("assets.id"), nullable=False
    )

    from_department_id = db.Column(db.Integer, nullable=False)
    to_department_id = db.Column(db.Integer, nullable=False)

    requested_by = db.Column(db.Integer, nullable=False)
    approved_by = db.Column(db.Integer)
    received_by = db.Column(db.Integer)

    request_date = db.Column(
        db.Date, nullable=False, default=date.today
    )
    approval_date = db.Column(db.Date)
    received_date = db.Column(db.Date)

    reason = db.Column(db.Text, nullable=False)
    remarks = db.Column(db.Text)

    status = db.Column(
        db.String(30), default="Pending", nullable=False
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    asset = db.relationship("Asset", backref="transfer_history")


# ============================================================================
# ASSET MAINTENANCE
# ============================================================================

class AssetMaintenance(db.Model):
    __tablename__ = "asset_maintenance"

    id = db.Column(db.Integer, primary_key=True)

    asset_id = db.Column(
        db.Integer, db.ForeignKey("assets.id"), nullable=False
    )

    maintenance_type = db.Column(db.String(50), nullable=False)
    priority = db.Column(
        db.String(30), default="Normal", nullable=False
    )
    description = db.Column(db.Text, nullable=False)

    requested_by = db.Column(db.Integer)
    approved_by = db.Column(db.Integer)
    technician_id = db.Column(db.Integer)

    request_date = db.Column(
        db.Date, nullable=False, default=date.today
    )
    service_date = db.Column(db.Date)
    completion_date = db.Column(db.Date)

    cost = db.Column(db.Numeric(14, 2), default=0)
    service_provider = db.Column(db.String(150))

    approval_remarks = db.Column(db.Text)
    completion_remarks = db.Column(db.Text)

    status = db.Column(
        db.String(30), default="Pending", nullable=False
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    asset = db.relationship("Asset", backref="maintenance_history")


# ============================================================================
# ASSET AUDIT LOG
# ============================================================================

class AssetAuditLog(db.Model):
    __tablename__ = "asset_audit_logs"

    id = db.Column(db.Integer, primary_key=True)

    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id"))
    user_id = db.Column(db.Integer)

    action = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)

    ip_address = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    asset = db.relationship("Asset")


# ============================================================================
# DEPARTMENT ASSET RECORD
# ============================================================================

class DepartmentAssetRecord(db.Model):
    __tablename__ = "department_asset_records"

    id = db.Column(db.Integer, primary_key=True)

    asset_id = db.Column(
        db.Integer, db.ForeignKey("assets.id"), nullable=False
    )

    department_id = db.Column(db.Integer, nullable=False)
    custodian_id = db.Column(db.Integer)

    allocation_date = db.Column(db.Date, nullable=False)
    remarks = db.Column(db.Text)

    status = db.Column(
        db.String(30), default="Active", nullable=False
    )

    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    asset = db.relationship("Asset")


# ============================================================================
# ASSET DEPRECIATION
# ============================================================================

class AssetDepreciation(db.Model):
    __tablename__ = "asset_depreciation"

    id = db.Column(db.Integer, primary_key=True)

    asset_id = db.Column(
        db.Integer, db.ForeignKey("assets.id"), nullable=False
    )

    financial_year = db.Column(db.String(20), nullable=False)

    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)

    opening_book_value = db.Column(db.Numeric(14, 2), nullable=False)
    depreciation_amount = db.Column(db.Numeric(14, 2), nullable=False)
    accumulated_depreciation = db.Column(
        db.Numeric(14, 2), nullable=False
    )
    closing_book_value = db.Column(db.Numeric(14, 2), nullable=False)

    calculation_method = db.Column(db.String(30), nullable=False)

    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    asset = db.relationship("Asset", backref="depreciation_records")

    __table_args__ = (
        db.UniqueConstraint(
            "asset_id",
            "financial_year",
            name="uq_asset_depreciation_year",
        ),
    )


# ============================================================================
# ASSET DISPOSAL
# ============================================================================

class AssetDisposal(db.Model):
    __tablename__ = "asset_disposals"

    id = db.Column(db.Integer, primary_key=True)

    asset_id = db.Column(
        db.Integer, db.ForeignKey("assets.id"), nullable=False
    )

    disposal_type = db.Column(db.String(50), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    condition_report = db.Column(db.Text)

    requested_by = db.Column(db.Integer, nullable=False)
    inspected_by = db.Column(db.Integer)
    approved_by = db.Column(db.Integer)
    finance_reviewed_by = db.Column(db.Integer)

    request_date = db.Column(
        db.Date, nullable=False, default=date.today
    )
    inspection_date = db.Column(db.Date)
    approval_date = db.Column(db.Date)
    disposal_date = db.Column(db.Date)

    estimated_value = db.Column(db.Numeric(14, 2), default=0)
    sale_proceeds = db.Column(db.Numeric(14, 2), default=0)
    book_value_at_disposal = db.Column(db.Numeric(14, 2), default=0)

    status = db.Column(
        db.String(30), default="Pending", nullable=False
    )
    remarks = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    asset = db.relationship("Asset", backref="disposal_history")


# ============================================================================
# ASSET VERIFICATION CAMPAIGN
# ============================================================================

class AssetVerificationCampaign(db.Model):
    __tablename__ = "asset_verification_campaigns"

    id = db.Column(db.Integer, primary_key=True)

    campaign_code = db.Column(
        db.String(50), unique=True, nullable=False
    )
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)

    department_id = db.Column(db.Integer, nullable=True)

    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date)

    created_by = db.Column(db.Integer, nullable=False)

    status = db.Column(
        db.String(30), default="Draft", nullable=False
    )

    approved_by = db.Column(db.Integer)
    approved_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    records = db.relationship(
        "AssetVerificationRecord",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )


class AssetVerificationRecord(db.Model):
    __tablename__ = "asset_verification_records"

    id = db.Column(db.Integer, primary_key=True)

    campaign_id = db.Column(
        db.Integer,
        db.ForeignKey("asset_verification_campaigns.id"),
        nullable=False,
    )
    asset_id = db.Column(
        db.Integer, db.ForeignKey("assets.id"), nullable=False
    )

    verification_status = db.Column(
        db.String(30), default="Pending"
    )
    physical_condition = db.Column(db.String(50))
    physical_location = db.Column(db.String(200))

    verified_by = db.Column(db.Integer)
    verified_at = db.Column(db.DateTime)

    remarks = db.Column(db.Text)

    reviewed_by = db.Column(db.Integer)
    reviewed_at = db.Column(db.DateTime)

    campaign = db.relationship(
        "AssetVerificationCampaign", back_populates="records"
    )
    asset = db.relationship("Asset")

    __table_args__ = (
        db.UniqueConstraint(
            "campaign_id",
            "asset_id",
            name="uq_campaign_asset_verification",
        ),
    )


# ============================================================================
# ASSET INSURANCE / WARRANTY / CLAIMS
# ============================================================================

class AssetInsurance(db.Model):
    __tablename__ = "asset_insurances"

    id = db.Column(db.Integer, primary_key=True)

    asset_id = db.Column(
        db.Integer, db.ForeignKey("assets.id"), nullable=False
    )

    policy_number = db.Column(
        db.String(100), unique=True, nullable=False
    )
    insurance_company = db.Column(
        db.String(200), nullable=False
    )
    insurance_type = db.Column(db.String(100))

    coverage_amount = db.Column(db.Numeric(15, 2), nullable=False)
    premium_amount = db.Column(db.Numeric(15, 2), default=0)

    start_date = db.Column(db.Date, nullable=False)
    expiry_date = db.Column(db.Date, nullable=False)

    contact_person = db.Column(db.String(150))
    contact_phone = db.Column(db.String(30))

    document_path = db.Column(db.String(300))

    status = db.Column(db.String(30), default="Active")

    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    asset = db.relationship("Asset", backref="insurance_policies")


class AssetWarranty(db.Model):
    __tablename__ = "asset_warranties"

    id = db.Column(db.Integer, primary_key=True)

    asset_id = db.Column(
        db.Integer, db.ForeignKey("assets.id"), nullable=False
    )

    warranty_provider = db.Column(db.String(200))
    warranty_reference = db.Column(db.String(100))
    warranty_type = db.Column(db.String(100))

    start_date = db.Column(db.Date)
    expiry_date = db.Column(db.Date, nullable=False)

    coverage_details = db.Column(db.Text)
    terms_and_conditions = db.Column(db.Text)

    document_path = db.Column(db.String(300))

    status = db.Column(db.String(30), default="Active")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    asset = db.relationship("Asset", backref="warranties")


class AssetInsuranceClaim(db.Model):
    __tablename__ = "asset_insurance_claims"

    id = db.Column(db.Integer, primary_key=True)

    insurance_id = db.Column(
        db.Integer,
        db.ForeignKey("asset_insurances.id"),
        nullable=False,
    )

    claim_number = db.Column(
        db.String(100), unique=True, nullable=False
    )

    incident_date = db.Column(db.Date, nullable=False)
    incident_type = db.Column(db.String(100))
    incident_description = db.Column(db.Text)

    claimed_amount = db.Column(db.Numeric(15, 2), nullable=False)
    approved_amount = db.Column(db.Numeric(15, 2), default=0)

    submitted_date = db.Column(db.Date)
    settlement_date = db.Column(db.Date)

    status = db.Column(db.String(30), default="Draft")
    remarks = db.Column(db.Text)

    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    insurance = db.relationship("AssetInsurance", backref="claims")


# ============================================================================
# ASSET DOCUMENTS
# ============================================================================

class AssetDocument(db.Model):
    __tablename__ = "asset_documents"

    id = db.Column(db.Integer, primary_key=True)

    asset_id = db.Column(
        db.Integer, db.ForeignKey("assets.id"), nullable=False
    )

    document_name = db.Column(db.String(200), nullable=False)
    document_type = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)

    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    file_extension = db.Column(db.String(20))
    file_size = db.Column(db.Integer)

    version = db.Column(db.Integer, default=1)
    expiry_date = db.Column(db.Date)
    is_confidential = db.Column(db.Boolean, default=False)

    status = db.Column(db.String(30), default="Active")

    uploaded_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    asset = db.relationship("Asset", backref="documents")


class AssetDocumentVersion(db.Model):
    __tablename__ = "asset_document_versions"

    id = db.Column(db.Integer, primary_key=True)

    document_id = db.Column(
        db.Integer,
        db.ForeignKey("asset_documents.id"),
        nullable=False,
    )

    version_number = db.Column(db.Integer, nullable=False)

    original_filename = db.Column(db.String(255))
    stored_filename = db.Column(db.String(255))
    file_path = db.Column(db.String(500))
    file_size = db.Column(db.Integer)

    uploaded_by = db.Column(db.Integer)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    document = db.relationship("AssetDocument", backref="versions")


# ============================================================================
# BACKWARD-COMPATIBILITY RE-EXPORT
# AssetApproval is defined in approval_models.py so the approval service
# and route modules can import it from either location.
# ============================================================================

from .approval_models import AssetApproval  # noqa: E402,F401