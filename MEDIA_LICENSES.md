# Free visual media policy

AutoYT uses original locally generated graphics, Pixabay Content License media, or Pexels License media. No paid stock provider, arbitrary web image scraper or YouTube downloader is used. When permitted stock is unavailable, the pipeline uses original graphics.

Pixabay allows free use and adaptation under its [Content License](https://pixabay.com/service/license-summary/). Pexels permits free personal and commercial use under its [License](https://www.pexels.com/license/). These are licenses with restrictions, not public-domain declarations or guarantees against copyright claims. Restrictions include standalone redistribution and certain uses involving people, brands and endorsement. Model/property rights, trademarks and third-party rights can still matter. Illustrative stock does not depict the actual reported event.

Each selected stock asset records its public source, creator attribution and license URL in `media_assets.json`. `media_license_audit.json` records the approved-policy check before rendering/validation; upload rechecks the records. Credits are included in the video description and visible in dashboard run details. A missing or unapproved record prevents upload. The software can check records and domains; it cannot independently prove contributor ownership or the absence of protected logos in every frame.

Locally imported media requires an explicit free license: **CC0-1.0** with `https://creativecommons.org/publicdomain/zero/1.0/` or **CC-BY-4.0** with `https://creativecommons.org/licenses/by/4.0/`. The local manifest must include `free_to_use: true`, the exact `license_url`, source, permitted use and creator attribution. Only mark an asset this way when those rights are actually documented. The example manifest is documentation, not a license to an included clip.

Pixabay search metadata is cached for 24 hours in line with its [API documentation](https://pixabay.com/api/docs/). API keys and download/runtime artifacts stay outside Git. Provider license terms can change; review the linked terms periodically. No stock music is added.
