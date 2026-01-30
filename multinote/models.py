"""
SQLAlchemy models for multiNote.
"""

from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


# Association table for Entry <-> Tag many-to-many
entry_tags = db.Table(
    "entry_tags",
    db.Column("entry_id", db.Integer, db.ForeignKey("entry.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tag.id"), primary_key=True),
)


class Entry(db.Model):
    """A single notebook entry (text, header, image, data, or code)."""

    # Entry type constants
    TYPE_TEXT = "text"
    TYPE_HEADER = "header"
    TYPE_IMAGE = "image"
    TYPE_DATA = "data"
    TYPE_CODE = "code"

    TYPES = [TYPE_TEXT, TYPE_HEADER, TYPE_IMAGE, TYPE_DATA, TYPE_CODE]

    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(20), nullable=False, default=TYPE_TEXT)
    title = db.Column(db.String(200), nullable=True)
    body = db.Column(db.Text, nullable=False, default="")

    # Author tracking (simple string for now, will become FK to User table)
    author = db.Column(db.String(100), nullable=False, default="Anonymous")
    edited_by = db.Column(db.String(100), nullable=True)  # Who last edited

    # Timestamps: created_at determines timeline position, edited_at tracks modifications
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    edited_at = db.Column(db.DateTime, nullable=True)  # None until first edit

    # Future: author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    # Future: edited_by_id for tracking who made edits

    # Relationships
    tags = db.relationship("Tag", secondary=entry_tags, back_populates="entries", lazy="dynamic")

    def __repr__(self):
        return f"<Entry {self.id} [{self.type}] {self.created_at}>"

    @property
    def is_edited(self):
        """Return True if this entry has been edited after creation."""
        return self.edited_at is not None

    @property
    def timestamp_display(self):
        """Return a human-friendly timestamp for creation time."""
        return self.created_at.strftime("%b %d, %Y %I:%M %p")

    @property
    def edited_at_display(self):
        """Return a human-friendly timestamp for edit time."""
        if self.edited_at:
            return self.edited_at.strftime("%b %d, %Y %I:%M %p")
        return None

    def mark_edited(self):
        """Update the edited_at timestamp."""
        self.edited_at = datetime.now(timezone.utc)


class Tag(db.Model):
    """A hashtag that can be associated with entries."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    entries = db.relationship("Entry", secondary=entry_tags, back_populates="tags", lazy="dynamic")

    def __repr__(self):
        return f"<Tag #{self.name}>"
