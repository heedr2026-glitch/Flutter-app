import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:untitled1/advertisement_banner.dart';


void main() {
  testWidgets('all formats keep the same height, reserved logo and bounded RTL text', (tester) async {
    const square = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAFAAAABQCAIAAAABc2X6AAAAdUlEQVR4nO3PAQ0AIRDAsOP9ewYXfDJaBdua2fOS7++A2wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1B9vBAZ8YuKa+AAAAAElFTkSuQmCC';
    const wide = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAPAAAAA8CAIAAADXHaAKAAAAvElEQVR4nO3SwQkAIBDAsNP9d9YlBKEkE/TRNXMGKvbvAHjJ0KQYmhRDk2JoUgxNiqFJMTQphibF0KQYmhRDk2JoUgxNiqFJMTQphibF0KQYmhRDk2JoUgxNiqFJMTQphibF0KQYmhRDk2JoUgxNiqFJMTQphibF0KQYmhRDk2JoUgxNiqFJMTQphibF0KQYmhRDk2JoUgxNiqFJMTQphibF0KQYmhRDk2JoUgxNiqFJMTQphibF0KQYmpQLoEMBd6rSWxsAAAAASUVORK5CYII=';
    const design = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAWgAAAB4CAIAAABQJv+tAAABh0lEQVR4nO3UQQ0AIBDAsAP/nkHDXoSkVbDX1swZgGK/DgD+YxxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAZhxAdgFm2wHvZzFoPAAAAABJRU5ErkJggg==';
    final cases = <Map<String, dynamic>>[
      {'title': 'عرض', 'message': 'خصم خاص', 'image_data': square},
      {'title': List.filled(20, 'عنوان طويل').join(' '), 'message': List.filled(100, 'نص عربي طويل').join(' '), 'image_data': square},
      {'title': 'شعار أفقي', 'message': 'عرض جديد', 'image_data': wide},
      {'title': 'هذا النص لا يظهر فوق التصميم', 'banner_config': {'adType': 'image', 'bannerImageData': design}},
    ];
    for (final width in <double>[240, 360, 600]) {
      for (final ad in cases) {
        await tester.pumpWidget(MaterialApp(home: Scaffold(body: Center(child: SizedBox(width: width, child: AdvertisementBannerView(ad: ad))))));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        final size = tester.getSize(find.byType(AdvertisementBannerView));
        expect(size.width, closeTo(width, .01));
        expect(size.height, closeTo(width / 3, .01));
        final full = ad.containsKey('banner_config');
        expect(find.byKey(const ValueKey('advertisement-logo-slot')), full ? findsNothing : findsOneWidget);
        if (full) expect(find.text(ad['title']), findsNothing);
        final images = tester.widgetList<Image>(find.byType(Image));
        expect(images.every((image) => image.fit == BoxFit.contain), isTrue);
      }
    }
  });
  testWidgets('mixed advertisements keep frame during transitions', (tester) async {
    for (var i = 0; i < 4; i++) {
      final ad = {'title': 'إعلان $i', 'message': List.filled(i * 20 + 1, 'نص').join(' ')};
      await tester.pumpWidget(MaterialApp(home: Scaffold(body: Center(child: SizedBox(width: 360, child: AdvertisementBannerSwitcher(ad: ad, identity: i))))));
      await tester.pump(const Duration(milliseconds: 400));
      expect(tester.getSize(find.byType(AdvertisementBannerSwitcher)).height, 120);
      expect(tester.takeException(), isNull);
      await tester.pumpAndSettle();
    }
  });
  test('rotation follows per advertisement durations', () {
    final ads = <Map<String, dynamic>>[{'display_seconds': 3}, {'display_seconds': 12}];
    expect(advertisementRotationIndex(ads, DateTime.fromMillisecondsSinceEpoch(2000)), 0);
    expect(advertisementRotationIndex(ads, DateTime.fromMillisecondsSinceEpoch(3000)), 1);
    expect(advertisementRotationIndex(ads, DateTime.fromMillisecondsSinceEpoch(15000)), 0);
  });
}
