# Creative visuals and sound

Production settings → Video style has three independent switches. Save video style to apply them to new automatic and manual videos. Saved jobs retain their selected switches; jobs created before these switches keep the old behavior on retry.

- **Animated icons:** Iconify’s bundled Lucide set, selected by scene topic and animated with Remotion. No icon API key or internet request is used during rendering.
- **Lottie animations:** Frame-synced @remotion/lottie playback in Motion Graphics and Hybrid motion scenes. A small original AutoYT pulse is included. Imported files with matching tags replace that pulse; unrelated files are not selected.
- **Subtle sound effects:** Original synthesized transitions or matching imported CC0 sounds. Works in all three video styles. At most six brief accents, spaced at least 1.5 seconds apart. FFmpeg ducks effects beneath narration and limits output peaks. Narration text and voice stay unchanged.

Normal scenes do not invoke Remotion. In Hybrid, footage scenes remain footage. If the configured Normal fallback is used, icon/Lottie credits are omitted because those assets were not rendered. Effects have their own switch and can still be mixed.

## Import LottieFiles animations

1. Download a **free JSON** animation from its original LottieFiles page. Verify the specific asset is offered under the Lottie Simple License. The library does not scrape pages or use a paid API.
2. In Production settings → Creative asset library → Add an animation or sound, select **LottieFiles · vector JSON**.
3. Select the JSON; add its name, creator, original HTTPS asset-page URL, and relevant comma-separated tags such as ai, coding, cloud.
4. Confirm the license and click **Import asset**.

Supported: vector-only JSON, up to 2 MB, 2048px, 20 seconds and 600 frames. Expressions, external images, fonts, text layers, effects and bundled .lottie archives are rejected. Choose a simpler vector animation if an export is rejected. Assets are decorative and are never presented as product screenshots or factual diagrams.

## Import Freesound effects

1. On Freesound, choose a short **CC0** sound and download WAV, MP3 or OGG. Check the sound’s own license; CC BY and CC BY-NC are not accepted by this integration.
2. Select **Freesound · CC0 audio** in the same import form. Add the sound file, name, creator, source page and topic tags.
3. Confirm CC0 and import. Files must be at most 8 MB and five seconds. FFmpeg validates and converts them into local mono 48 kHz PCM WAV files. Only the first 0.8 seconds is used for an accent, with fades and headroom.

No Freesound API key is required. This integration uses downloaded files: Freesound’s free API terms restrict API use to non-commercial purposes even when an individual sound is CC0. Commercial API access requires arranging a license with Freesound. No account registration, paid API, or new subscription is created here.

Imported files and their manifest live in ignored data/creative/. The manifest records source, creator, license, tags and file hash. Content is validated again before rendering. Credits for assets actually used appear in the saved job’s render_report.json, the combined license audit, and dashboard run details. The library supports 50 imports. It reads only local files; a source URL is provenance, never a download instruction.

For another laptop, install motion dependencies with scripts/setup_motion.ps1 and copy the ignored creative library separately if desired. Package data includes the original pulse and Lucide license notice. Install uses exact package versions and the lockfile.

References: [Iconify API](https://iconify.design/docs/api/), [Lucide ISC license](https://lucide.dev/license), [Remotion Lottie](https://www.remotion.dev/docs/lottie/lottie), [LottieFiles licensing](https://help.lottiefiles.com/animation-licensing-basics-), [Freesound API terms](https://freesound.org/docs/api/terms_of_use.html).

For real video backgrounds and the local footage library, see [Hybrid footage](hybrid-footage.md).
