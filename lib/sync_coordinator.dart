import 'sync_journal.dart';
import 'sync_merge.dart';

class SyncRemoteRecord {
  final int revision;
  final SyncValue value;
  const SyncRemoteRecord(this.revision, this.value);
}

class SyncUnauthorized implements Exception {}

class SyncRevisionConflict implements Exception {}

class SyncSessionStopped implements Exception {}

class SyncRemotePage {
  final Map<String, SyncRemoteRecord> records;
  final String? nextCursor;
  SyncRemotePage(Map<String, SyncRemoteRecord> records, this.nextCursor)
    : records = Map.unmodifiable(records);
}

abstract interface class PagedSyncTransport implements ScopedSyncTransport {
  Future<SyncRemotePage> list(String collection, String? cursor);
}

class SyncPullResult {
  final int applied, deferred;
  const SyncPullResult(this.applied, this.deferred);
}

/// Prototype contract: production adapters must use a verified login and bind
/// requests to this immutable account/branch, never a mutable global token.
abstract interface class ScopedSyncTransport {
  SyncScope get scope;
  Future<SyncRemoteRecord> put(
    String collection,
    String id,
    int baseRevision,
    SyncValue value,
  );
  Future<SyncRemoteRecord?> get(String collection, String id);
}

/// Local-only coordinator. No HTTP adapter or application data hooks yet.
/// One record at a time; uncertain writes remain durable for exact retry.
class SyncCoordinator {
  final SyncJournal journal;
  final ScopedSyncTransport transport;
  bool _stopped = false;
  bool _busy = false;
  SyncCoordinator(this.journal, this.transport) {
    if (journal.scope.key != transport.scope.key) {
      throw ArgumentError('Transport account/branch does not match journal');
    }
  }

  /// Call immediately on logout, branch/account change or session expiry.
  /// A stopped coordinator cannot be reused, even after signing in again.
  void stop() => _stopped = true;

  void _check() {
    if (_stopped || journal.scope.key != transport.scope.key) {
      stop();
      throw SyncSessionStopped();
    }
  }

  /// Bounded scan, not a point-in-time snapshot. Restart after errors;
  /// never delete by absence. Resolve deferred uploads via syncRecord.
  Future<SyncPullResult> pullCollection(
    String collection, {
    int maxPages = 100,
  }) async {
    _check();
    if (_busy) throw StateError('Synchronization already running');
    if (maxPages < 1 || maxPages > 100) {
      throw ArgumentError('Invalid page limit');
    }
    if (!{
      'branches',
      'vehicles',
      'employees',
      'appointments',
      'documents',
      'organization',
    }.contains(collection)) {
      throw ArgumentError('Unsupported collection');
    }
    final source = transport;
    if (source is! PagedSyncTransport) throw StateError('Paging unavailable');
    _busy = true;
    var applied = 0, deferred = 0;
    String? cursor;
    try {
      for (var n = 0; n < maxPages; n++) {
        _check();
        final page = await source.list(collection, cursor);
        _check();
        if (page.records.length > 200) throw StateError('Oversized page');
        var previous = cursor;
        for (final item in page.records.entries) {
          if (!RegExp(r'^[a-zA-Z0-9_-]{1,100}$').hasMatch(item.key) ||
              (previous != null && item.key.compareTo(previous) <= 0) ||
              item.value.revision < 1 ||
              (item.value.value.deleted && item.value.value.data.isNotEmpty)) {
            throw StateError('Invalid or out-of-order page');
          }
          previous = item.key;
        }
        if (page.nextCursor != null &&
            (page.records.isEmpty || page.nextCursor != previous)) {
          throw StateError('Invalid pagination cursor');
        }
        for (final item in page.records.entries) {
          _check();
          final local = await journal.inspect(collection, item.key);
          _check();
          if (local?['flight'] != null) {
            deferred++;
            continue;
          }
          await journal.reconcile(
            collection,
            item.key,
            item.value.revision,
            item.value.value,
          );
          applied++;
        }
        if (page.nextCursor == null) return SyncPullResult(applied, deferred);
        cursor = page.nextCursor;
      }
      throw StateError('Page limit reached; scan incomplete');
    } on SyncUnauthorized {
      stop();
      rethrow;
    } finally {
      _busy = false;
    }
  }

  Future<void> syncRecord(String collection, String id) async {
    _check();
    if (_busy) throw StateError('Synchronization already running');
    _busy = true;
    try {
      final flight = await journal.beginUpload(collection, id);
      _check();
      if (flight != null) {
        final raw = flight['value'] as Map;
        SyncRemoteRecord result;
        try {
          result = await transport.put(
            collection,
            id,
            flight['baseRevision'] as int,
            SyncValue(
              deleted: raw['deleted'] as bool,
              data: Map<String, dynamic>.from(raw['data'] as Map),
            ),
          );
        } on SyncRevisionConflict {
          _check();
          final remote = await transport.get(collection, id);
          _check();
          if (remote == null) {
            throw StateError(
              'Conflicting record missing; retain pending write',
            );
          }
          await journal.reconcile(
            collection,
            id,
            remote.revision,
            remote.value,
            rejectedUpload: true,
          );
          return;
        }
        _check();
        await journal.acknowledge(
          collection,
          id,
          flight,
          result.revision,
          result.value,
        );
      } else {
        final remote = await transport.get(collection, id);
        _check();
        // Absence is never an instruction to delete a local record.
        if (remote != null) {
          await journal.reconcile(
            collection,
            id,
            remote.revision,
            remote.value,
          );
        }
      }
    } on SyncUnauthorized {
      stop();
      rethrow;
    } finally {
      _busy = false;
    }
  }
}
