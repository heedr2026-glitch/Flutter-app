import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../lib/branch_store.dart';
import '../lib/branch_pages.dart';
import '../lib/home_branches_switch.dart';
import '../lib/my_advertisements.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(
    () =>
        SharedPreferences.setMockInitialValues({'session_user_type': 'admin'}),
  );
  test(
    'home branches require opt in and two branches; employees cannot switch',
    () async {
      final prefs = await BranchPreferences.getInstance();
      expect(showHomeBranchButton(prefs), false);
      await prefs.setBool('show_home_branches', true);
      expect(showHomeBranchButton(prefs), false);
      await prefs.addBranch('فرع الرياض');
      expect(showHomeBranchButton(prefs), true);
      await prefs.setBool('show_home_branches', false);
      expect(showHomeBranchButton(prefs), false);
      expect(BranchPreferences.branches(prefs.raw), hasLength(2));
      await prefs.setBool('show_home_branches', true);
      await prefs.setString('session_user_type', 'employee');
      expect(showHomeBranchButton(prefs), false);
    },
  );
  testWidgets('home without opt in has no branches button or spacer', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: CategoriesBranchActions(
            onCategories: () {},
            onBranchChanged: () {},
          ),
        ),
      ),
    );
    expect(find.text('الفروع'), findsNothing);
    expect(find.byType(BranchSelector), findsNothing);
    expect(find.text('التصنيفات'), findsOneWidget);
  });
  testWidgets('settings switch persists and does not create branches', (
    tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: HomeBranchesSwitch())),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byType(Switch));
    await tester.pumpAndSettle();
    final prefs = await BranchPreferences.getInstance();
    expect(prefs.getBool('show_home_branches'), true);
    expect(BranchPreferences.branches(prefs.raw), hasLength(1));
    expect(showHomeBranchButton(prefs), false);
  });
  test('canonical approval flags do not appear pending when status is absent or stale', () {
    expect(advertisementStatus({'approved': 1, 'active': 1}), 'published');
    expect(
      advertisementStatus({
        'approved': true,
        'active': true,
        'status': 'pending',
      }),
      'published',
    );
    expect(advertisementStatus({'approved': 0, 'active': 1}), 'pending');
    expect(
      advertisementStatus({
        'approved': 1,
        'active': 0,
        'expires_at': '2000-01-01T00:00:00Z',
      }),
      'expired',
    );
    expect(advertisementStatus({}), 'unknown');
  });
  test('VIP employees can view public advertisement banners', () {
    expect(
      showPublicAdvertisementBanner(
        subscriptionPackage: 'vip',
        isEmployee: true,
      ),
      isTrue,
    );
    expect(
      showPublicAdvertisementBanner(
        subscriptionPackage: 'vip',
        isEmployee: false,
      ),
      isFalse,
    );
  });
  testWidgets('restored ad card shows open button and fits within 220px', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(320, 700);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    var refreshed = false;
    var opened = false;
    await tester.pumpWidget(
      MaterialApp(
        home: Directionality(
          textDirection: TextDirection.rtl,
          child: Scaffold(
            body: Padding(
              padding: const EdgeInsets.all(16),
              child: CompactAdvertisementSummary(
                latest: {'id': 2, 'status': 'pending', 'title': 'طلب تجديد'},
                published: {'id': 1, 'status': 'published', 'expires_at': null},
                updatedAt: DateTime(2030, 1, 1, 10),
                onOpen: () => opened = true,
                onRefresh: () => refreshed = true,
              ),
            ),
          ),
        ),
      ),
    );
    expect(find.textContaining('منشور'), findsOneWidget);
    expect(find.text('آخر طلب #2: قيد المراجعة'), findsOneWidget);
    expect(
      tester.getSize(find.byType(Card)).height,
      inInclusiveRange(140, 200),
    );
    expect(find.text('فتح إعلاني'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.tap(find.byTooltip('تحديث حالة الإعلان'));
    expect(refreshed, true);
    await tester.tap(find.text('فتح إعلاني'));
    expect(opened, true);
  });
}
