import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'sync_merge.dart';

/// Offline prototype only. No application records or network APIs are wired in.
/// Caller must derive this scope from a verified login, never a form/cache value.
class SyncScope {
  final String server, organization, branch;
  SyncScope(this.server, this.organization, this.branch) {
    final uri = Uri.parse(server);
    if (uri.scheme != 'https' ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment ||
        organization.isEmpty ||
        branch.isEmpty) {
      throw ArgumentError('Invalid verified synchronization scope');
    }
  }
  String get key => jsonEncode([server, organization, branch]);
}

abstract interface class SyncSnapshotStore {
  Future<String?> read();
  Future<void> append(String snapshot);
}

/// Append-only snapshots; interrupted/corrupt records fail closed, never reset.
/// One locked file per scope. Retention/compaction is deliberately not enabled.
class FileSyncSnapshotStore implements SyncSnapshotStore {
  final RandomAccessFile _file;
  static const maxBytes = 16 * 1024 * 1024;
  bool _unusable = false;
  FileSyncSnapshotStore._(this._file);
  static Future<FileSyncSnapshotStore> open(File file) async {
    final handle = await file.open(mode: FileMode.append);
    try {
      await handle.lock(FileLock.exclusive);
      return FileSyncSnapshotStore._(handle);
    } catch (_) {
      await handle.close();
      rethrow;
    }
  }

  @override
  Future<String?> read() async {
    final length = await _file.length();
    if (length == 0) return null;
    if (length > maxBytes) throw StateError('Journal exceeds local limit');
    await _file.setPosition(0);
    final text = utf8.decode(await _file.read(length));
    if (!text.endsWith('\n')) {
      throw const FormatException(
        'Interrupted journal; preserve file for recovery',
      );
    }
    final lines = text.substring(0, text.length - 1).split('\n');
    for (final line in lines) {
      if (jsonDecode(line) is! Map) {
        throw const FormatException('Invalid journal snapshot');
      }
    }
    return lines.last;
  }

  @override
  Future<void> append(String snapshot) async {
    if (_unusable) throw StateError('Reopen journal after uncertain write');
    final bytes = utf8.encode('$snapshot\n');
    final length = await _file.length();
    if (length + bytes.length > maxBytes) {
      throw StateError('Journal full; no edit was committed');
    }
    try {
      await _file.setPosition(length);
      await _file.writeFrom(bytes);
      await _file.flush();
    } catch (_) {
      _unusable = true;
      rethrow;
    }
  }

  Future<void> close() async {
    try {
      await _file.unlock();
    } finally {
      await _file.close();
    }
  }
}

Map<String, dynamic> _copy(Map value) =>
    Map<String, dynamic>.from(jsonDecode(jsonEncode(value)) as Map);
Map<String, dynamic> _encodeValue(SyncValue value) {
  if (value.deleted && value.data.isNotEmpty) {
    throw ArgumentError('Tombstones contain no data');
  }
  return _copy({'deleted': value.deleted, 'data': value.data});
}

SyncValue _value(dynamic raw) {
  if (raw is! Map || raw['deleted'] is! bool || raw['data'] is! Map) {
    throw const FormatException('Invalid sync value');
  }
  final value = SyncValue(
    deleted: raw['deleted'] as bool,
    data: _copy(raw['data'] as Map),
  );
  if (value.deleted && value.data.isNotEmpty) {
    throw const FormatException('Invalid tombstone');
  }
  return value;
}

/// Stores baseline, pending intent and in-flight request as a single commit.
/// Serialized edits never acknowledge a newer edit using an older HTTP response.
class SyncJournal {
  final SyncScope scope;
  final SyncSnapshotStore store;
  Map<String, dynamic> _entries = {};
  Future<void> _tail = Future<void>.value();
  SyncJournal._(this.scope, this.store);
  static Future<SyncJournal> open(
    SyncScope scope,
    SyncSnapshotStore store,
  ) async {
    final journal = SyncJournal._(scope, store);
    final raw = await store.read();
    if (raw != null) {
      final state = jsonDecode(raw);
      if (state is! Map ||
          state['version'] != 1 ||
          state['scope'] != scope.key ||
          state['entries'] is! Map) {
        throw const FormatException(
          'Wrong account or unsupported/corrupt journal',
        );
      }
      journal._entries = _copy(state['entries'] as Map);
      for (final entry in journal._entries.values) {
        if (entry is! Map ||
            entry['generation'] is! int ||
            entry['revision'] is! int ||
            entry['revision'] < 0) {
          throw const FormatException('Invalid journal record');
        }
        _value(entry['local']);
        if (entry['base'] != null) _value(entry['base']);
        if (entry['revision'] > 0 && entry['base'] == null) {
          throw const FormatException('Missing baseline');
        }
        if (entry['flight'] != null) _value(entry['flight']['value']);
        if (entry['conflict'] != null) _value(entry['conflict']['value']);
      }
    }
    return journal;
  }

  String _key(String collection, String id) {
    if (!RegExp(r'^[a-zA-Z0-9_-]{1,100}$').hasMatch(id) ||
        !{
          'branches',
          'vehicles',
          'employees',
          'appointments',
          'documents',
          'organization',
        }.contains(collection)) {
      throw ArgumentError('Unsupported record identity');
    }
    return '$collection/$id';
  }

  Future<T> _edit<T>(T Function(Map<String, dynamic>) action) {
    final result = _tail.then((_) async {
      final next = _copy(_entries);
      final value = action(next);
      await store.append(
        jsonEncode({'version': 1, 'scope': scope.key, 'entries': next}),
      );
      _entries = next;
      return value;
    });
    _tail = result.then<void>((_) {}, onError: (Object _, StackTrace _) {});
    return result;
  }

  Future<Map<String, dynamic>?> inspect(String collection, String id) async {
    await _tail;
    final entry = _entries[_key(collection, id)];
    return entry == null ? null : _copy(entry as Map);
  }

  Future<void> stage(String collection, String id, SyncValue local) {
    final key = _key(collection, id), value = _encodeValue(local);
    return _edit((entries) {
      final entry = entries.putIfAbsent(
        key,
        () => <String, dynamic>{'generation': 0, 'revision': 0, 'base': null},
      );
      entry['local'] = value;
      entry['generation'] = (entry['generation'] as int) + 1;
    });
  }

  Future<Map<String, dynamic>?> beginUpload(String collection, String id) =>
      _edit((entries) {
        final entry = entries[_key(collection, id)];
        if (entry == null || entry['conflict'] != null) return null;
        if (entry['flight'] == null) {
          if (entry['base'] != null &&
              _value(entry['base']).fingerprint ==
                  _value(entry['local']).fingerprint) {
            return null;
          }
          entry['flight'] = {
            'generation': entry['generation'],
            'baseRevision': entry['revision'],
            'value': _copy(entry['local']),
          };
        }
        return _copy(entry['flight']);
      });
  Future<void> acknowledge(
    String collection,
    String id,
    Map<String, dynamic> sent,
    int revision,
    SyncValue remote,
  ) {
    final expected = _copy(sent), value = _encodeValue(remote);
    return _edit((entries) {
      final entry = entries[_key(collection, id)];
      if (entry == null ||
          jsonEncode(entry['flight']) != jsonEncode(expected) ||
          revision != expected['baseRevision'] + 1 ||
          _value(value).fingerprint != _value(expected['value']).fingerprint) {
        throw StateError('Stale or mismatched upload acknowledgement');
      }
      entry['revision'] = revision;
      entry['base'] = value;
      entry.remove('flight');
      // local may already contain a newer edit; deliberately retain it.
    });
  }

  Future<void> reconcile(
    String collection,
    String id,
    int revision,
    SyncValue remote, {
    bool rejectedUpload = false,
  }) {
    if (revision < 1) throw ArgumentError('Remote revision must be positive');
    final key = _key(collection, id), value = _encodeValue(remote);
    return _edit((entries) {
      final entry = entries[key];
      if (entry == null) {
        entries[key] = {
          'generation': 0,
          'revision': revision,
          'base': value,
          'local': value,
        };
        return;
      }
      if (entry['flight'] != null && !rejectedUpload) {
        throw StateError('Resolve uncertain upload before pulling');
      }
      if (revision < entry['revision']) {
        throw StateError('Refusing stale remote revision');
      }
      final conflict = entry['conflict'];
      if (conflict != null &&
          (revision < conflict['revision'] ||
              (revision == conflict['revision'] &&
                  _value(conflict['value']).fingerprint !=
                      _value(value).fingerprint))) {
        throw StateError('Refusing stale or inconsistent conflict revision');
      }
      if (revision == entry['revision'] &&
          entry['base'] != null &&
          _value(entry['base']).fingerprint != _value(value).fingerprint) {
        throw StateError('Remote content changed without a revision');
      }
      final decision = decideSync(
        base: entry['base'] == null ? null : _value(entry['base']),
        local: _value(entry['local']),
        remote: _value(value),
      );
      if (rejectedUpload) entry.remove('flight');
      if (decision == SyncDecision.conflict) {
        entry['conflict'] = {
          'revision': revision,
          'value': value,
          'generation': entry['generation'],
        };
        return;
      }
      entry.remove('conflict');
      entry['revision'] = revision;
      entry['base'] = value;
      if (decision == SyncDecision.download) entry['local'] = value;
    });
  }

  Future<void> resolve(
    String collection,
    String id, {
    required int expectedRevision,
    required int expectedGeneration,
    required bool keepLocal,
  }) => _edit((entries) {
    final entry = entries[_key(collection, id)];
    final conflict = entry?['conflict'];
    if (conflict == null ||
        conflict['revision'] != expectedRevision ||
        entry['generation'] != expectedGeneration) {
      throw StateError('Conflict changed while the user was reviewing it');
    }
    entry['revision'] = conflict['revision'];
    entry['base'] = conflict['value'];
    if (!keepLocal) entry['local'] = conflict['value'];
    entry.remove('conflict');
  });
}
