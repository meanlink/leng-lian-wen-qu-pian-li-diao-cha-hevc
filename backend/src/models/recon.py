from sqlalchemy import String, Double, ForeignKey, Integer, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class ReconSheet(Base):
    """双源核对单：开单即固定两源预期点位与对应关系。"""

    __tablename__ = 'recon_sheets'
    __table_args__ = (UniqueConstraint('open_key', name='uq_recon_sheets_open_key'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    note: Mapped[str] = mapped_column(String(500), default='')
    primary_label: Mapped[str] = mapped_column(String(40), default='主记录器')
    backup_label: Mapped[str] = mapped_column(String(40), default='备记录器')
    status: Mapped[str] = mapped_column(String(20), default='open')
    revision: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=0)
    point_count: Mapped[int] = mapped_column(Integer, default=0)
    definition_checksum: Mapped[str] = mapped_column(String(64), default='')
    open_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    sealed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    batch_dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'), nullable=True)


class ReconPoint(Base):
    """核对点位：两源来源键在开单时固定。原值一经接收不再覆盖。"""

    __tablename__ = 'recon_points'
    __table_args__ = (
        UniqueConstraint('sheet_id', 'seq', name='uq_recon_points_seq'),
        UniqueConstraint('sheet_id', 'sample', name='uq_recon_points_sample'),
        UniqueConstraint('sheet_id', 'primary_key', name='uq_recon_points_primary'),
        UniqueConstraint('sheet_id', 'backup_key', name='uq_recon_points_backup'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey('recon_sheets.id'))
    seq: Mapped[int] = mapped_column(Integer)
    sample: Mapped[str] = mapped_column(String(64))
    route: Mapped[str] = mapped_column(String(64))
    minute: Mapped[float] = mapped_column(Double)
    primary_key: Mapped[str] = mapped_column(String(64))
    backup_key: Mapped[str] = mapped_column(String(64))
    primary_value: Mapped[float | None] = mapped_column(Double, nullable=True)
    backup_value: Mapped[float | None] = mapped_column(Double, nullable=True)
    primary_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    backup_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='waiting')
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    adopted_value: Mapped[float | None] = mapped_column(Double, nullable=True)
    note: Mapped[str] = mapped_column(String(300), default='')
    decided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    decided_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class ReconDifference(Base):
    """核对单差异记录：重复原值不记，改变原值或晚到异值才记。"""

    __tablename__ = 'recon_differences'

    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey('recon_sheets.id'))
    point_id: Mapped[int | None] = mapped_column(ForeignKey('recon_points.id'), nullable=True)
    source: Mapped[str] = mapped_column(String(20))
    sample: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Double)
    kind: Mapped[str] = mapped_column(String(30))
    adopted_value: Mapped[float | None] = mapped_column(Double, nullable=True)
    note: Mapped[str] = mapped_column(String(300), default='')
    request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    at: Mapped[float] = mapped_column(Double)


class ReconReceipt(Base):
    """核对单操作回执：同一 request_id 重试返回原结果，不重复落账。"""

    __tablename__ = 'recon_receipts'
    __table_args__ = (UniqueConstraint('sheet_id', 'action', 'request_id', name='uq_recon_receipts_request'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey('recon_sheets.id'))
    action: Mapped[str] = mapped_column(String(20))
    request_id: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Double)


class ReconRun(Base):
    """封单后写入可调查批次的执行记录；旧执行不得覆盖新裁定。"""

    __tablename__ = 'recon_runs'
    __table_args__ = (UniqueConstraint('sheet_id', 'revision', name='uq_recon_runs_revision'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey('recon_sheets.id'))
    revision: Mapped[int] = mapped_column(Integer)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='running')
    detail: Mapped[str] = mapped_column(String(500), default='')
    created_at: Mapped[float] = mapped_column(Double)
    finished_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class ReconAdoption(Base):
    """采用依据：每次裁定/封单留一条，保留裁定当时的两源原值与采用值。"""

    __tablename__ = 'recon_adoptions'

    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey('recon_sheets.id'))
    point_id: Mapped[int] = mapped_column(ForeignKey('recon_points.id'))
    decision: Mapped[str] = mapped_column(String(20))
    adopted_value: Mapped[float | None] = mapped_column(Double, nullable=True)
    primary_value: Mapped[float | None] = mapped_column(Double, nullable=True)
    backup_value: Mapped[float | None] = mapped_column(Double, nullable=True)
    note: Mapped[str] = mapped_column(String(300), default='')
    revision: Mapped[int] = mapped_column(Integer, default=0)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    decided_at: Mapped[float] = mapped_column(Double)
