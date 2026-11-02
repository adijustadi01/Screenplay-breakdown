"""Step 3: merges are proposed, never silent; accepted merges can be undone."""
from sqlalchemy import select

from app.models import Appearance, CastingRole, Character, Location, ReviewItem
from app.models.enums import EntityStatus
from app.reconciliation.service import propose
from app.review.service import act_on_item

from .helpers import pipeline


def _proposals(db, v):
    return db.scalars(select(ReviewItem).where(ReviewItem.script_version_id == v.id,
                                               ReviewItem.category == "reconciliation")).all()


def test_expected_proposals_and_nothing_merged_yet(db, project):
    v = pipeline(db, project)
    made = propose(db, v.id)
    assert made == {"casting_variant": 2, "character_alias": 1, "location_variant": 1, "location_parent": 1}
    assert all(c.status == EntityStatus.ACTIVE for c in db.scalars(select(Character)))
    assert all(loc.status == EntityStatus.ACTIVE for loc in db.scalars(select(Location)))
    # Re-running does not duplicate proposals.
    assert propose(db, v.id) == {}


def test_casting_variant_accept_and_undo(db, project):
    v = pipeline(db, project)
    propose(db, v.id)
    item = next(i for i in _proposals(db, v) if i.proposal["action"] == "casting_variant"
                and db.get(Character, i.proposal["variant_id"]).canonical_name == "YOUNG ARJUN")
    act_on_item(db, item.id, "accept", "AD")
    arjun = db.scalars(select(Character).where(Character.canonical_name == "ARJUN")).one()
    young = db.scalars(select(Character).where(Character.canonical_name == "YOUNG ARJUN")).one()
    roles = {r.name for r in db.scalars(select(CastingRole).where(CastingRole.character_id == arjun.id))}
    assert roles == {"ARJUN", "YOUNG ARJUN"}
    assert young.status == EntityStatus.MERGED and young.merged_into_id == arjun.id
    young_apps = db.scalars(select(Appearance).join(CastingRole, Appearance.casting_role_id == CastingRole.id)
                            .where(CastingRole.name == "YOUNG ARJUN")).all()
    assert len(young_apps) == 2 and all(a.character_id == arjun.id for a in young_apps)

    act_on_item(db, item.id, "undo", "AD")
    db.refresh(young)
    assert young.status == EntityStatus.ACTIVE
    assert len(db.scalars(select(Appearance).where(Appearance.character_id == young.id)).all()) == 2
    assert not db.scalars(select(CastingRole).where(CastingRole.character_id == arjun.id)).all()
    assert db.get(ReviewItem, item.id).status.value == "open"


def test_location_variant_merge_moves_children_and_undo(db, project):
    v = pipeline(db, project)
    propose(db, v.id)
    item = next(i for i in _proposals(db, v) if i.proposal["action"] == "merge_location")
    src = db.get(Location, item.proposal["from_id"])
    assert src.name == "ARJUNS HOUSE"
    bedroom = db.scalars(select(Location).where(Location.name == "BEDROOM")).one()
    act_on_item(db, item.id, "accept", "AD")
    assert db.get(Location, bedroom.id).parent_id == item.proposal["into_id"]
    act_on_item(db, item.id, "undo", "AD")
    assert db.get(Location, bedroom.id).parent_id == src.id


def test_declined_proposal_stays_declined(db, project):
    v = pipeline(db, project)
    propose(db, v.id)
    item = next(i for i in _proposals(db, v) if i.proposal["action"] == "reparent_location")
    act_on_item(db, item.id, "decline", "AD")
    assert propose(db, v.id) == {}
    assert db.scalars(select(Location).where(Location.name == "HOSPITAL ROOFTOP")).one().parent_id is None
