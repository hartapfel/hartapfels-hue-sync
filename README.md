# Hartapfel's Hue Sync

Sync Philips Hue lights with the picture or sound on an LG C9. The TV captures the screen locally and streams colors to the lights in your Hue Entertainment area over your home network. You can close the setup screen once sync is running.

## What you need

- An LG C9 with Homebrew Channel and service elevation available. Direct capture needs access to the TV’s capture hardware.
- A Hue Bridge and an Entertainment area configured in the Hue app. Place the lights in the area where they sit around your screen.
- A PC with webOS Dev Manager connected to the TV to install the package.

## Install and pair

1. Download the webOS `.ipk` from [Releases](https://github.com/hartapfel/hartapfels-hue-sync/releases/latest) and install it on the C9 using webOS Dev Manager. Installing over a previous version keeps your pairing and settings.
2. Grant the capture service access through Homebrew Channel. Run this on the TV using the Dev Manager terminal.

   ```sh
   luna-send-pub -n 1 luna://org.webosbrew.hbchannel.service/elevateService '{"id":"org.webosbrew.c9screensync.capture"}'
   ```

3. Open **Hartapfel's Hue Sync**. Enter your bridge IP address, press the physical link button on the bridge, then select **Pair bridge**.
4. Choose your Entertainment area, select **Direct TV capture**, and choose **Start sync**. Stop any other app using the same area or capture device first.

## Using sync

**Video** and **Gaming** sample the video and graphics layers together, including TV app interfaces and menus. Each mode uses its own screen regions and response settings. **Music** follows bass, midrange, treble and stereo balance from the TV’s audio output. Audio capture depends on firmware support. Brightness sets the light output and intensity controls how quickly it reacts.

Enable the startup option to sync automatically when the TV is ready. On the C9 the setting is labeled **Start sync on cold boot**. A normal TV power-off releases the Entertainment area so you can control the lights in the Hue app again.

If you already use PicCap, you can select **PicCap stream** and send TCP frames to `127.0.0.1:19401` on the TV. Stop PicCap capture before switching back to Direct TV capture.

## Development

The [GitHub Actions workflow](.github/workflows/webos.yml) builds and tests the ARM workers and packages the app and its two webOS services. It pins the desktop color reference, capture backends and compiler versions. The direct capture service uses the DILE video and GM graphics backends shipped with [PicCap 0.5.3](https://github.com/TBSniller/piccap/releases/tag/0.5.3). Only regional color samples pass to the service that holds the Hue pairing.
