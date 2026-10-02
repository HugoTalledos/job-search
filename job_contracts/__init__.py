"""Data exchanged between the local collector and the remote agent."""

from .models import CollectionReport, CollectorPlan, JobLead, JobPosting, SearchPlan, SearchQuery, SourceCollection

__all__ = [
    "CollectionReport", "CollectorPlan", "JobLead", "JobPosting", "SearchPlan", "SearchQuery", "SourceCollection",
]
