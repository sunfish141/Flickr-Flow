# Water barriers

`north-america-water.geojson.gz` bundles Natural Earth **5.1.2**, at
**1:10 million map scale** (not 10 metre resolution). It contains land, lakes,
and supplementary North American lakes, clipped to the application's bounds
(-179, 24, -50, 84). It is 1.9 MB and requires no network requests at inference.

Natural Earth data is [public domain](https://www.naturalearthdata.com/about/terms-of-use/).
The [lake dataset](https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-lakes/)
and coastline geometry are generalized. Small ponds, streams, and fine shoreline
detail may be absent. This is a water barrier, not a vegetation or fuel map.

`sources.json` records pinned download URLs, source checksums, the transformation
recipe, and the bundled artifact checksum. The transformation preserves polygon
coordinates, including lake holes/islands, after clipping and geometry repair.
The geometry is independent of map-tile colors and of elevation.
