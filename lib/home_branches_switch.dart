import 'package:flutter/material.dart';

import 'branch_store.dart';

bool showHomeBranchButton(BranchPreferences prefs) =>
    prefs.getBool('show_home_branches') == true &&
    prefs.getString('session_user_type') != 'employee' &&
    BranchPreferences.branches(prefs.raw).length > 1;

class HomeBranchesSwitch extends StatefulWidget {
  const HomeBranchesSwitch({super.key});
  @override
  State<HomeBranchesSwitch> createState() => _HomeBranchesSwitchState();
}

class _HomeBranchesSwitchState extends State<HomeBranchesSwitch> {
  bool _enabled = false;
  bool _busy = true;
  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final prefs = await BranchPreferences.getInstance();
    if (mounted)
      setState(() {
        _enabled = prefs.getBool('show_home_branches') == true;
        _busy = false;
      });
  }

  Future<void> _change(bool value) async {
    setState(() => _busy = true);
    try {
      final prefs = await BranchPreferences.getInstance();
      if (prefs.getString('session_user_type') == 'employee') return;
      if (!await prefs.setBool('show_home_branches', value))
        throw StateError('save');
      if (mounted) setState(() => _enabled = value);
    } catch (_) {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('تعذر حفظ الخيار، حاول مرة أخرى')),
        );
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => SwitchListTile(
    title: const Text(
      'إظهار زر الفروع في الرئيسية',
      style: TextStyle(color: Colors.white),
    ),
    subtitle: const Text(
      'يظهر بعد إضافة فرع ثانٍ. إيقافه لا يحذف أي فرع أو بيانات.',
      style: TextStyle(color: Colors.white70),
    ),
    value: _enabled,
    onChanged: _busy ? null : _change,
  );
}
