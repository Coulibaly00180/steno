"""Templates: single default, updated_at, starter templates (F-12.5).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-22
"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

LEGACY_DEFAULT_NAME = "Compte-rendu standard"
MEETING_NAME = "Compte-rendu de réunion"

# Frozen copies: never import app code in a migration.
MEETING_PROMPT = """# Résumé exécutif
Un résumé court de l'essentiel.

# Points clés
Liste structurée des faits et idées importantes.

# Décisions
Décisions explicites prises dans le contenu. Si aucune, l'indiquer.

# Actions
Actions à réaliser, responsables et échéances lorsqu'ils sont mentionnés.

# Détails
Résumé plus complet et organisé par thème, sans inventer d'information.
"""

STARTER_TEMPLATES = [
    (
        "Cours / formation",
        "Objectifs, notions clés, exemples, points à retenir et questions de révision.",
        """# Objectifs
Ce que le cours cherche à faire comprendre ou maîtriser.

# Notions clés
Chaque notion importante, avec une définition courte.

# Exemples
Exemples, démonstrations ou cas pratiques présentés.

# Points à retenir
Les idées essentielles à mémoriser.

# Questions de révision
Trois à cinq questions pour vérifier la compréhension, fondées uniquement sur le contenu.
""",
    ),
    (
        "Podcast / interview",
        "Intervenants, thèmes, citations marquantes, idées clés et ressources.",
        """# Intervenants et sujet
Qui parle et de quoi traite l'épisode.

# Thèmes abordés
Les sujets dans l'ordre où ils apparaissent.

# Citations marquantes
Citations exactes, avec leur horodatage lorsqu'il est disponible.

# Idées clés
Les idées ou conseils principaux.

# Ressources mentionnées
Livres, sites, outils ou personnes cités.
""",
    ),
    (
        "Présentation / démo",
        "Problème, solution, fonctionnalités, chiffres, questions du public et suites.",
        """# Problème
Le problème ou le besoin présenté.

# Solution présentée
Ce qui est proposé pour y répondre.

# Fonctionnalités
Les fonctionnalités montrées ou annoncées.

# Chiffres cités
Chiffres, métriques et dates mentionnés, sans arrondi.

# Questions du public
Questions posées et réponses apportées.

# Prochaines étapes
Suites annoncées, avec échéances lorsqu'elles sont mentionnées.
""",
    ),
]

templates = sa.table(
    "summary_templates",
    sa.column("id", sa.String),
    sa.column("name", sa.String),
    sa.column("description", sa.Text),
    sa.column("prompt", sa.Text),
    sa.column("is_default", sa.Boolean),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.add_column("summary_templates", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    # Keep only the oldest default before the unique index can be created.
    defaults = bind.execute(
        sa.select(templates.c.id).where(templates.c.is_default.is_(True)).order_by(templates.c.created_at)
    ).scalars().all()
    for extra in defaults[1:]:
        bind.execute(templates.update().where(templates.c.id == extra).values(is_default=False))

    names = set(bind.execute(sa.select(templates.c.name)).scalars())
    if LEGACY_DEFAULT_NAME in names and MEETING_NAME not in names:
        # Renamed only if the user never renamed it.
        bind.execute(templates.update().where(templates.c.name == LEGACY_DEFAULT_NAME).values(name=MEETING_NAME))
        names.add(MEETING_NAME)
    if MEETING_NAME not in names:
        bind.execute(templates.insert().values(
            id=str(uuid.uuid4()), name=MEETING_NAME,
            description="Résumé exécutif, points clés, décisions, actions et détails.",
            prompt=MEETING_PROMPT, is_default=not defaults, created_at=now,
        ))
    elif not defaults:
        bind.execute(templates.update().where(templates.c.name == MEETING_NAME).values(is_default=True))
    for name, description, prompt in STARTER_TEMPLATES:
        if name not in names:
            bind.execute(templates.insert().values(
                id=str(uuid.uuid4()), name=name, description=description,
                prompt=prompt, is_default=False, created_at=now,
            ))

    op.create_index(
        "uq_summary_templates_single_default",
        "summary_templates",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
        sqlite_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index("uq_summary_templates_single_default", table_name="summary_templates")
    with op.batch_alter_table("summary_templates") as batch:
        batch.drop_column("updated_at")
    # Starter templates are user data from now on: they are kept.
