# Common Types of Spatial Artifacts in Gridded Quantitative Precipitation Estimates (QPE)

Gridded QPE combines remotely-sensed estimates of precipitation, from satellite and radar, with surface-based gauge observations to provide a continuous surface of rainfall over a given spatial domain and temporal range.  Radar and satellite remote-sensed observations are key in providing estimates over otherwise data-sparse regions, while rain gauges at the surface provide the necessary “ground-truthing” of indirect observations. Conversely, remotely-sensed data can also help overcome physical limitations of rain gauges, such as wind undercatch, where high winds prevent precipitation falling directly in a gauge, or tipping-bucket delays, when very light rainfall occurs but accumulations are too small to tip the bucket and record an observation. <br><br>
The [NWS Analysis of Record for Calibration (AORC)](https://registry.opendata.aws/noaa-nws-aorc/) dataset provides 1-km spatial resolution and 1-hourly temporal resolution estimates of rainfall from 1979 – present, providing a sufficient period of record for use in detailed hydrologic, hydrometeorological, and climate studies. Like all earth observations, gridded QPE has its own complicated set of biases and potential errors that should be considered and reviewed before application in studies. Some artifacts may lead to slight over or underestimation of rainfall that may not necessarily drastically impact use-cases, while some artifacts indicate serious issues with data quality that should be addressed or removed.<br><br>
The following is a summary of common QPE spatial artifacts that have been identified in the AORC dataset, but it is not an exhaustive list. This document will be regularly updated as new issues are identified. When in doubt about the quality of a storm derived from AORC QPE, best practice is to compare with other available datasets, such [PRISM]( https://prism.oregonstate.edu/), while keeping in mind that alternative may also have their own quirks. <br><br>

| Artifact Origin | Artifact Type | Visual Clues |
| :--- | :--- | :--- |
| Radar | Terrain Features and Beam Blockage | Wedge-shaped “shadows” or data voids |
| Radar | Range Degradation (Bottom-of-Beam (BOB) Rings) | Concentric rings or arcs at locations of known BOB heights |
| Radar | False Observations | Catch-all for odd “storm” behavior |
| Radar | Reflectivity Spikes | Uncharacteristically high, localized precipitation |
| Satellite | Grid Cell Splicing | Visible seams |
| Satellite | Course Resolution | Overly smooth spatial rainfall |
| Gauge | Interpolation Rings | Concentric rings or bullseyes around gauge locations |
| Grid-mosaicking | RFC Boundaries | Step-wise changes in precipitation across known boundaries, persisting across multiple storms |



## Radar Artifacts
**Terrain Features and Beam Blockage**  occur when mountains, tall buildings, or other topography physically block the radar beam from propagating. Sometimes even very intense rainfall can cause beam blockage as signal attenuation occurs through downpours. Visually, this creates wedge-shaped “shadows” or data voids that radiate outward from site of the blockage. <br><br>
**Range Degradation or 'Bottom-of-Beam Rings'** - as distance from the radar site increases, the radar beam naturally rises higher in the atmosphere due to the curvature of the Earth, causing the radar to miss shallow or low-level precipitation. This can be visually detected by concentric rings or arcs in [known locations](https://www.roc.noaa.gov/branches/program-branch/site-id-database/site-id-location-maps.php) where precipitation is systematically lower.<br><br>

**False Observations**  occur when non-hydrometeors reflect the radar’s energy beam back to the radar antenna are observed as precipitation. This can include stationary objects like wind farms, or moving objects like swarms of birds and bugs, or highway traffic. Most radar algorithms can detect and filter this, but it is imperfect. This can manifest visually in multiple ways, such as
* Stationary very high estimates, like that from wind turbines
* “Storms” that pop up right at sunrise when bird flocks take off when there is otherwise no other weather around.
* Ground clutter or speckle noise, which looks like random areas of adjacent high and low precipitation not associated with a storm system<br>

**Reflectivity Spikes** occur when a radar beam interacts with a highly reflective target, like hail, and cause artificially high Reflectivity-Rainfall (ZR) relationships. Visually this can look like uncharacteristically high, localized peaks in the broader precipitation field.
  * Similarly, storms with multi-phase precipitation (rain and snow, freezing rain) can result in massive overestimations in locations where the beam is interacting with the area of phase-shift (i.e. freezing level)

## Satellite Artifacts
**Grid Cell Splicing** occurs when merging polar-orbiting and geostationary satellite swathes together that may have slight mismatches in timestamps, angles, or coverage boundaries. Visually this appears as artificial “seams” or visible pixeled jumps along borders where the data from the satellite passes intersect. <br><br>
**Course Resolution** - because satellite sensors cover much larger domains than radars, their spatial grid cells are also larger. The large pixel size can average out or smooth localized rainfall and produce systematic low biases in rainfall estimates. This is especially true in older QPE products that rely more heavily on satellite estimates before NEXRAD radars were installed across the US in the 1990s-2000s. <br><br>


## Gauge Artifacts
**Interpolation Artifacts** – gauge observations represent conditions at a single point and interpolation algorithms are then used to spread that point-source measurement across a spatial grid. Common methods of gauge interpolation are inverse distance weighting (IDW) and kriging. When gauge-density is sparse, the interpolation can often produce “bullseyes” or visible concentric rings around isolated stations. This is particularly severe when a gauge captures localized heavy rainfall compared to surrounding areas or nearby gauges. <br><br>

## Grid-mosaicking Artifacts
**[River Forecast Center](https://www.weather.gov/gis/RFCBounds) (RFC) Boundaries** Unique to the AORC dataset, the nation-wide grid of precipitation is produced by individual River Forecast Centers covering their own region and then mosaicked together. Because adjacent RFCs may use slightly different quality control procedures, gauge weightings, or local radar algorithms, this can produce artificial step-changes that are visible directly along RFC boundaries and persist across storm events.

## South Platte Examples
Here are examples of some of the above QPE spatial artifacts found when reviewing the South Platte storm catalog
<img width="1264" height="642" alt="SP_QPEartificat_examples" src="https://github.com/user-attachments/assets/77bac774-b534-4036-b87d-bfc19f292e54" />

