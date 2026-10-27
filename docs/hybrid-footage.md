# Hybrid footage workflow

Hybrid now preserves the selected source media in animated scenes, including the opening and closing shots. Licensed video is normalized locally, muted, and rendered as the full-frame background; photo scenes use camera movement. Headline reveals, icons and Lottie elements are brief overlays. Generic branding and stock-footage watermarks are omitted; creator and license credits remain in job artifacts.

Selection order is local video, local photo, configured stock providers, public photos, then original graphics. Pixabay searches moving footage before stills and accepts landscape HD clips for vertical cropping. Abstract AI/coding scene descriptions can match concrete computer-work footage; unrelated subjects are not rewritten to coding. Assets are not repeated within the same video.

The current ignored local library contains three Pexels programming clips by Mikhail Nilov and one CC0 laptop photo. It covers computer-work and coding explainers. Additional topics need appropriately tagged licensed files in assets/footage/manifest.json or a configured stock provider. The user selected local-library use for now; no API credentials were added.

Local records need file, kind (stock or stock_image), source_url, license, attribution, permitted_use, and descriptive tags. Paths must remain inside the manifest folder. Downloaded source files and the manifest are ignored by Git. Copy this library separately when transferring to another laptop.

Style reference: https://www.youtube.com/shorts/_YgoFc6dGmI (Plivo). Used as an editing reference; the preview footage is independently licensed from Pexels and Wikimedia Commons.

The shared saved default is Hybrid with icons, Lottie and sound effects enabled, used by scheduled runs and manual runs that use the saved mode. Captions use bold white sentence-case text, a vivid blue keyword rectangle, and a subtle dark outline over the footage. Brief headlines use the same bold white/blue treatment. Arial Bold is preferred on Windows and Liberation Sans Bold on Linux, with Indic fallbacks preserved; these approximate the reference's weight and layout rather than identifying its proprietary font. Caption phrase timings are estimated within measured narration scenes. Explicit per-run mode choices remain available; captions share this design in every mode.
