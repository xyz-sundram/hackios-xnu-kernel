/* Phone (1), native portrait pixels. AOSPA Android device overlay, commit
 * 6e4ad6b994a51348104acf3b1c22162ccde9da21:
 * overlay/Phone1Frameworks/res/values/config.xml,
 * config_mainBuiltInDisplayCutoutRectApproximation (top-left origin).
 * https://github.com/AOSPA/android_device_nothing_phone1/blob/6e4ad6b994a51348104acf3b1c22162ccde9da21/overlay/Phone1Frameworks/res/values/config.xml
 */
#ifndef HAKIOS_SPACEWAR_CUTOUT_H
#define HAKIOS_SPACEWAR_CUTOUT_H
#define SPACEWAR_PANEL_WIDTH_PX 1080.0
#define SPACEWAR_CUTOUT_RIGHT_PX 187.83
#define SPACEWAR_CUTOUT_BOTTOM_PX 135.65

/* UIKit uses logical points. Keep its original inset in landscape, on an
 * external display, or when its existing inset already clears the cutout. */
static double
spacewar_statusbar_leading(double original, double bar_width, double screen_width, double screen_height)
{
    double points;
    long rounded;
    if (!(screen_width > 0 && screen_height > screen_width &&
          bar_width > screen_width - 1 && bar_width < screen_width + 1))
        return original;
    points = SPACEWAR_CUTOUT_RIGHT_PX * bar_width / SPACEWAR_PANEL_WIDTH_PX;
    rounded = (long)points;
    if ((double)rounded < points) rounded++;
    return original > rounded ? original : (double)rounded;
}
#endif
