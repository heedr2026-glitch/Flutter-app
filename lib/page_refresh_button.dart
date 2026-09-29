import 'package:flutter/material.dart';

/// Reloads page data without replacing the route or discarding user input.
class PageRefreshButton extends StatefulWidget {
  const PageRefreshButton({
    super.key,
    required this.onRefresh,
    this.confirmReload = false,
  });
  final Future<void> Function() onRefresh;
  final bool confirmReload;

  @override
  State<PageRefreshButton> createState() => _PageRefreshButtonState();
}

class _PageRefreshButtonState extends State<PageRefreshButton> {
  bool _busy = false;

  Future<void> _refresh() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      if (widget.confirmReload) {
        final confirmed = await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: const Text('تحديث البيانات المحفوظة؟'),
            content: const Text(
              'سيتم تحميل آخر بيانات محفوظة. أي تعديلات لم تحفظها في هذه الصفحة ستُستبدل.',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(dialogContext, false),
                child: const Text('إلغاء'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(dialogContext, true),
                child: const Text('تحديث'),
              ),
            ],
          ),
        );
        if (confirmed != true || !mounted) return;
      }
      await widget.onRefresh();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('تعذر التحديث، حاول مرة أخرى')),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => IconButton(
    tooltip: 'تحديث الصفحة',
    onPressed: _busy ? null : _refresh,
    icon: _busy
        ? const SizedBox(
            width: 20,
            height: 20,
            child: CircularProgressIndicator(strokeWidth: 2),
          )
        : const Icon(Icons.refresh),
  );
}
