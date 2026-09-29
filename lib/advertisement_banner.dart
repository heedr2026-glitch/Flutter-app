import 'dart:convert';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/material.dart';

const advertisementBannerAspectRatio = 3.0;
const advertisementBannerTransitionDuration = Duration(milliseconds: 1200);

int advertisementDisplaySeconds(dynamic value) =>
    (int.tryParse('$value') ?? 8).clamp(3, 60);

int advertisementRotationIndex(List<Map<String, dynamic>> ads, DateTime at) {
  if (ads.isEmpty) return 0;
  final total = ads.fold<int>(
    0, (sum, ad) => sum + advertisementDisplaySeconds(
      ad['display_seconds'] ?? ad['displaySeconds'],
    ),
  );
  var elapsed = (at.millisecondsSinceEpoch ~/ 1000) % total;
  for (var i = 0; i < ads.length; i++) {
    final seconds = advertisementDisplaySeconds(
      ads[i]['display_seconds'] ?? ads[i]['displaySeconds'],
    );
    if (elapsed < seconds) return i;
    elapsed -= seconds;
  }
  return 0;
}

Map<String, dynamic> advertisementBannerConfig(Map<String, dynamic> ad) {
  final raw = ad['banner_config'] ?? ad['bannerConfig'];
  if (raw is Map) return {...ad, ...Map<String, dynamic>.from(raw)};
  if (raw is String && raw.isNotEmpty) {
    try {
      final parsed = jsonDecode(raw);
      if (parsed is Map) return {...ad, ...Map<String, dynamic>.from(parsed)};
    } catch (_) {}
  }
  return ad;
}

Uint8List? _image(dynamic data) {
  if (data is! String || data.isEmpty) return null;
  try { return base64Decode(data.split(',').last); } catch (_) { return null; }
}

Color _color(dynamic raw, Color fallback) {
  final value = '$raw';
  if (!RegExp(r'^#[0-9a-fA-F]{6}$').hasMatch(value)) return fallback;
  return Color(int.parse('FF${value.substring(1)}', radix: 16));
}

/// The same fixed frame is used in the subscriber home and every final preview.
class AdvertisementBannerView extends StatelessWidget {
  final Map<String, dynamic> ad;
  const AdvertisementBannerView({super.key, required this.ad});

  @override
  Widget build(BuildContext context) {
    final config = advertisementBannerConfig(ad);
    final textColor = _color(config['textColor'], Colors.white);
    final barColor = _color(config['barColor'], const Color(0xFF172554));
    final fullImage = config['adType'] == 'image' || config['mode'] == 'full_image';
    final imageCandidates = fullImage
        ? [config['bannerImageData'], config['banner_image_data'],
            ad['image_data'], ad['imageData']]
        : [ad['image_data'], ad['imageData']];
    final bytes = _image(imageCandidates.firstWhere(
      (value) => value is String && value.isNotEmpty, orElse: () => null,
    ));
    Widget image(BoxFit fit) => bytes == null
        ? Center(child: Icon(Icons.campaign, color: textColor))
        : Image.memory(bytes, fit: fit,
            width: double.infinity, height: double.infinity,
            errorBuilder: (_, _, _) => Center(
              child: Icon(Icons.broken_image_outlined, color: textColor),
            ));
    return AspectRatio(
      aspectRatio: advertisementBannerAspectRatio,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: barColor,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: const Color(0xFFF59E0B), width: 1.5),
        ),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(14),
          child: fullImage ? Padding(
            padding: const EdgeInsets.all(1.5), child: image(BoxFit.contain),
          ) : LayoutBuilder(builder: (context, frame) {
            final unit = frame.maxWidth / 360;
            final padding = 14 * unit;
            final height = frame.maxHeight - 16 * unit;
            final font = (double.tryParse('${config['fontSize']}') ?? 18)
                .clamp(12, 32).toDouble();
            final titleSize = math.min(font * unit, height * .17);
            final bodySize = math.min((font - 3).clamp(12, 28).toDouble() * unit, height * .135);
            final actionSize = height * .095;
            final align = config['textAlign'] == 'left' ? TextAlign.left
                : config['textAlign'] == 'center' ? TextAlign.center : TextAlign.right;
            final scale = (double.tryParse('${config['logoScale']}') ?? 1)
                .clamp(.5, 1.5).toDouble();
            final position = config['logoPosition'] ?? 'left';
            final logo = SizedBox(
              key: const ValueKey('advertisement-logo-slot'),
              width: frame.maxWidth * .23, height: height,
              child: Center(child: SizedBox(
                width: math.min(72 * scale * unit, frame.maxWidth * .23),
                height: math.min(72 * scale * unit, height),
                child: image(BoxFit.contain),
              )),
            );
            final title = '${ad['title'] ?? ''}'.trim();
            final message = '${ad['message'] ?? ''}'.trim();
            final copy = Expanded(child: Directionality(
              textDirection: TextDirection.rtl,
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(title.isEmpty ? 'إعلان' : title, maxLines: 2,
                    overflow: TextOverflow.ellipsis, textAlign: align,
                    textScaler: TextScaler.noScaling,
                    style: TextStyle(color: textColor, fontSize: titleSize,
                      fontWeight: FontWeight.bold, height: 1.25)),
                  SizedBox(height: height * .03),
                  Text(message.isEmpty ? '${ad['advertiser'] ?? ad['organization_name'] ?? ''}' : message,
                    maxLines: 2, overflow: TextOverflow.ellipsis, textAlign: align,
                    textScaler: TextScaler.noScaling,
                    style: TextStyle(color: textColor.withValues(alpha: .82),
                      fontSize: bodySize, height: 1.4)),
                  SizedBox(height: height * .03),
                  Text(ad['promotion'] == 'packages' || ad['ad_source'] == 'platform'
                      ? 'عرض الباقات والاستفادة من الكود' : 'عرض تفاصيل الإعلان',
                    maxLines: 1, overflow: TextOverflow.ellipsis, textAlign: align,
                    textScaler: TextScaler.noScaling,
                    style: TextStyle(color: const Color(0xFFFBBF24),
                      fontSize: actionSize, height: 1.2, fontWeight: FontWeight.bold)),
                ],
              ),
            ));
            return Padding(
              padding: EdgeInsets.symmetric(horizontal: padding, vertical: 8 * unit),
              child: Row(textDirection: TextDirection.ltr,
                children: position == 'right'
                    ? [copy, SizedBox(width: 10 * unit), logo]
                    : [logo, SizedBox(width: 10 * unit), copy],
              ),
            );
          }),
        ),
      ),
    );
  }
}

class AdvertisementBannerSwitcher extends StatelessWidget {
  final Map<String, dynamic> ad;
  final Object identity;
  const AdvertisementBannerSwitcher({super.key, required this.ad, required this.identity});

  @override
  Widget build(BuildContext context) => AspectRatio(
    aspectRatio: advertisementBannerAspectRatio,
    child: ClipRect(child: AnimatedSwitcher(
      duration: advertisementBannerTransitionDuration,
      transitionBuilder: (child, animation) => FadeTransition(
        opacity: animation,
        child: SlideTransition(
          position: Tween<Offset>(begin: const Offset(.15, 0), end: Offset.zero)
              .animate(animation), child: child,
        ),
      ),
      child: AdvertisementBannerView(key: ValueKey(identity), ad: ad),
    )),
  );
}
