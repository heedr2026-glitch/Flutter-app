import 'package:flutter/material.dart';

import 'community_page.dart';
import 'main.dart' show DashboardPage, MyBusinessPage;
import 'video_ads/offers_feed_page.dart';

/// الشريط السفلي الثابت: الرئيسية | مشاهدة | المجتمع | مؤسستي.
class KhdoomHomeShell extends StatefulWidget {
  const KhdoomHomeShell({super.key, this.initialTab = 0});

  final int initialTab;

  @override
  State<KhdoomHomeShell> createState() => _KhdoomHomeShellState();
}

class _KhdoomHomeShellState extends State<KhdoomHomeShell> {
  late int _tab = widget.initialTab;
  // كل تبويب ينبني أول ما يفتحه المستخدم بس، ويبقى محفوظ بعدها.
  late final Set<int> _opened = {widget.initialTab};

  Widget _page(int index) {
    if (!_opened.contains(index)) return const SizedBox.shrink();
    switch (index) {
      case 1:
        return OffersFeedPage(active: _tab == 1);
      case 2:
        return const CommunityPage();
      case 3:
        return const MyBusinessPage();
      default:
        return const DashboardPage();
    }
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: _tab == 0,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop && _tab != 0) setState(() => _tab = 0);
      },
      child: Scaffold(
        body: IndexedStack(
          index: _tab,
          children: [for (var i = 0; i < 4; i++) _page(i)],
        ),
        bottomNavigationBar: Directionality(
          textDirection: TextDirection.rtl,
          child: NavigationBarTheme(
            // نفس ألوان الصفحة الرئيسية الداكنة.
            data: NavigationBarThemeData(
              backgroundColor: const Color(0xFF111B35),
              surfaceTintColor: Colors.transparent,
              indicatorColor: const Color(0xFF38BDF8),
              iconTheme: WidgetStateProperty.resolveWith(
                (states) => IconThemeData(
                  color: states.contains(WidgetState.selected)
                      ? const Color(0xFF0B1020)
                      : Colors.white70,
                ),
              ),
              labelTextStyle: WidgetStateProperty.resolveWith(
                (states) => TextStyle(
                  fontSize: 12,
                  color: states.contains(WidgetState.selected)
                      ? const Color(0xFF38BDF8)
                      : Colors.white70,
                  fontWeight: states.contains(WidgetState.selected)
                      ? FontWeight.bold
                      : FontWeight.normal,
                ),
              ),
            ),
            child: NavigationBar(
            selectedIndex: _tab,
            onDestinationSelected: (i) => setState(() {
              _tab = i;
              _opened.add(i);
            }),
            destinations: const [
              NavigationDestination(
                icon: Icon(Icons.home_outlined),
                selectedIcon: Icon(Icons.home),
                label: 'الرئيسية',
              ),
              NavigationDestination(
                icon: Icon(Icons.movie_outlined),
                selectedIcon: Icon(Icons.movie),
                label: 'مشاهدة',
              ),
              NavigationDestination(
                icon: Icon(Icons.forum_outlined),
                selectedIcon: Icon(Icons.forum),
                label: 'المجتمع',
              ),
              NavigationDestination(
                icon: Icon(Icons.business_outlined),
                selectedIcon: Icon(Icons.business),
                label: 'مؤسستي',
              ),
            ],
          ),
          ),
        ),
      ),
    );
  }
}
