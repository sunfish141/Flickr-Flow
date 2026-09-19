# Feature availability

| Capability | Required resources |
| --- | --- |
| Map, coordinate placement, inspection and timeline | Bundled React assets |
| Coarse learned simulation | Trusted frontier model; CSV terrain or ETOPO provider |
| Train/evaluate/predict offline models | Complete `htn_training/` public CSV release |
| Live satellite initialization | Server-side FIRMS key and provider connectivity |
| Historical daily comparisons | Retained complete FIRMS source archive |
| Vegetation inspector | Verified local vegetation stores/rasters |
| Fine fuel/road simulation | Native fuel bundles or NALCMS plus offline indexed roads |

After public-CSV training, coarse placement works without another repository's
source code or raw archive. Weather inference is available as a batch command
over observed examples, not as map forecast weather. Historical/vegetation/fine
capabilities report absent sources rather than synthesizing them from labels.
