import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:untitled1/main.dart';

void main() {
  testWidgets('settings exposes the public account-deletion page', (
    tester,
  ) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(const MaterialApp(home: SettingsPage()));
    await tester.pumpAndSettle();

    await tester.scrollUntilVisible(
      find.text('حذف الحساب'),
      300,
      scrollable: find.byType(Scrollable).first,
    );
    expect(find.text('حذف الحساب'), findsOneWidget);
    expect(Uri.parse(accountDeletionUrl).scheme, 'https');
    expect(Uri.parse(accountDeletionUrl).path, '/delete-account');
  });
}
