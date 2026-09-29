import 'dart:convert';

/// Pure three-way comparison. No writes or remote calls occur in this module.
/// The caller must persist the local/base pair before attempting uploads, and
/// scope it by authenticated organization, branch, collection and record id.
enum SyncDecision { unchanged, download, upload, conflict }

class SyncValue {
  final bool deleted;
  final Map<String, dynamic> data;
  const SyncValue({required this.deleted, required this.data});

  String get fingerprint => jsonEncode([deleted, _canonical(data)]);

  static dynamic _canonical(dynamic value) {
    if (value is Map) {
      final keys = value.keys.cast<String>().toList()..sort();
      return {for (final key in keys) key: _canonical(value[key])};
    }
    if (value is List) return value.map(_canonical).toList();
    return value;
  }
}

bool _same(SyncValue? a, SyncValue? b) => a?.fingerprint == b?.fingerprint;

SyncDecision decideSync({
  required SyncValue? base,
  required SyncValue? local,
  required SyncValue? remote,
}) {
  // Absence is not deletion: only an explicit tombstone may delete a record.
  if (local == null) {
    return remote == null ? SyncDecision.unchanged : SyncDecision.download;
  }
  if (_same(local, remote)) return SyncDecision.unchanged;
  if (remote == null) {
    return base == null ? SyncDecision.upload : SyncDecision.conflict;
  }
  if (_same(local, base)) return SyncDecision.download;
  if (_same(remote, base)) return SyncDecision.upload;
  return SyncDecision.conflict;
}
