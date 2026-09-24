"""Transactional usage helpers. Account permission is not proof of machine use.

All helpers except usage_transaction leave commit/rollback to their caller.
New usage dates are UTC-naive, unlike legacy Account.last_login_date.
"""
from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from app.database import Account, AccountStatus, Application, DeviceUsage, User, UserStatus, ApplicationPurpose


def usage_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def usage_iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def usage_record(record):
    return {
        'id': record.id, 'status': record.status, 'reason': record.reason,
        **{key: usage_iso(getattr(record, key)) for key in
           ('requested_at', 'started_at', 'confirmed_at', 'ended_at')},
    }


@contextmanager
def usage_transaction(db):
    """One atomic write; duplicate/racing requests return a safe conflict."""
    try:
        yield
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Registration changed or already exists; refresh and try again') from None
    except OperationalError:
        db.rollback()
        raise HTTPException(503, 'Database is busy or unavailable; try again later') from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(500, 'Unable to save registration; no changes were committed') from None
    except Exception:
        db.rollback()
        raise


def lock_usage_user(db, user_id, require_active=True):
    """Serialize grants/registrations even when there is no usage row to lock."""
    if db.get_bind().dialect.name == 'sqlite':
        db.execute(update(User).where(User.id == user_id).values(id=User.id))
    target = db.query(User).filter(User.id == user_id).populate_existing().with_for_update().first()
    if not target:
        raise HTTPException(404, 'Target user not found')
    if require_active and target.status != UserStatus.ACTIVE:
        raise HTTPException(409, 'Target user must be active')
    return target


def find_usage(db, user_id, server_id):
    # Do not refresh here: revocation helpers may be called repeatedly before
    # flush/commit, and refreshing would discard an in-flight end transition.
    return db.query(DeviceUsage).filter_by(user_id=user_id, server_id=server_id).first()


def usable_account(db, user_id, server_id, need_sudo=False):
    # DIRTY/UPDATING describe sync progress, not revoked permission. Revocation
    # is represented by is_login_able=False (or an inactive user).
    query = db.query(Account).join(User).filter(
        Account.user_id == user_id, Account.server_id == server_id,
        Account.is_login_able.is_(True), User.status == UserStatus.ACTIVE,
    )
    if need_sudo:
        query = query.filter(Account.is_sudo.is_(True))
    return query.first()


def grant_account(db, target, server, need_sudo=False):
    """Grant without demoting existing sudo; gateways always follow user role."""
    if target.status != UserStatus.ACTIVE:
        raise HTTPException(409, 'Target user must be active')
    account = db.query(Account).filter_by(user_id=target.id, server_id=server.id).first()
    if account is None:
        account = Account(user_id=target.id, server_id=server.id,
                          is_sudo=target.is_admin if server.is_gateway else need_sudo,
                          is_login_able=True, status=AccountStatus.DIRTY)
        db.add(account)
    else:
        account.is_sudo = target.is_admin if server.is_gateway else (account.is_sudo or need_sudo)
        account.is_login_able = True
        account.status = AccountStatus.DIRTY
        # Preserve the legacy regrant grace period; usage dates do not touch it.
        account.last_login_date = datetime.now()
    return account


def register_usage(db, target, server, state, reason, application=None, existing=None):
    """Reuse ended/rejected/cancelled rows (unique latest registration per pair)."""
    record = existing if existing is not None else find_usage(db, target.id, server.id)
    if record is None:
        record = DeviceUsage(user_id=target.id, server_id=server.id)
        db.add(record)
    now = usage_now()
    record.status = state
    record.reason = reason
    record.application_id = application.id if application is not None else None
    record.requested_at = now
    record.started_at = now if state == 'active' else None
    record.confirmed_at = now if state == 'active' else None
    record.ended_at = None
    return record


def activate_application_usage(db, application):
    """Resolve a linked registration, or register an approved legacy request."""
    if application.server.is_gateway:
        return None
    record = find_usage(db, application.user_id, application.server_id)
    if record is not None and record.status == 'active':
        # Duplicate legacy requests may exist: never reset an active session.
        record.application_id = None
        return record
    if record is not None and record.status == 'pending':
        now = usage_now()
        record.status = 'active'
        record.application_id = None
        record.started_at = now
        # Approval is an administrator action, not the user's self-confirmation.
        record.confirmed_at = None
        record.ended_at = None
        return record
    record = register_usage(db, application.user, application.server, 'active', '原有申请', existing=record)
    record.confirmed_at = None
    return record


def reject_application_usage(db, application):
    record = find_usage(db, application.user_id, application.server_id)
    if record is not None and record.status == 'pending' and record.application_id == application.id:
        record.status = 'rejected'
        record.application_id = None
        record.ended_at = usage_now()
    return record


def end_account_usage(db, user_id, server_id, reason=None):
    """End active registration when permission is revoked; NEVER commits.

    Caller should hold the same user lock for concurrent permission changes.
    Optional reason is a system explanation, not a replacement of the user's
    original request reason (there is no audit-reason column in this model).
    """
    cancel_sudo_application(db, user_id, server_id)
    record = find_usage(db, user_id, server_id)
    if record is not None and record.status == 'active':
        record.status = 'ended'
        record.ended_at = usage_now()
        record.application_id = None
    return record


def pending_sudo_application(db, user_id, server_id):
    return db.query(Application).join(ApplicationPurpose, Application.id == ApplicationPurpose.application_id).filter(
        Application.user_id == user_id, Application.server_id == server_id, ApplicationPurpose.kind == 'sudo'
    ).first()


def delete_application(db, application):
    """Explicit cleanup also works with SQLite connections lacking FK actions."""
    purpose = db.get(ApplicationPurpose, application.id)
    if purpose is not None:
        db.delete(purpose)
    db.delete(application)


def cancel_sudo_application(db, user_id, server_id):
    application = pending_sudo_application(db, user_id, server_id)
    if application is not None:
        delete_application(db, application)
    return application
