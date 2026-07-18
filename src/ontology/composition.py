from src.core.database.base import BaseDatabaseManager
from src.indexing.domain.ontology import OntologyShadowPort
from src.ontology.repository import PostgresOntologyRepository
from src.ontology.service import OntologyShadowService


def create_ontology_shadow(
    db_manager: BaseDatabaseManager,
) -> OntologyShadowPort:
    return OntologyShadowService(PostgresOntologyRepository(db_manager))
