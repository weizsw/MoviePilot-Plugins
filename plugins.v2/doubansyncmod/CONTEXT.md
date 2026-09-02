# DoubanSyncMod v2

DoubanSyncMod v2 turns a user's Douban “want to watch” feed into media acquisition requests. Eligible items can be assigned exclusively to MoviePilot or Seerr.

## Language

**Seerr routing filter**:
The criteria that assign an eligible Douban item to Seerr. Media type, original language, release-year range, and genre exclusion are combined with AND; an unconfigured criterion is unrestricted.

**Eligible item**:
A recognized Douban “want to watch” movie or TV item that is not already present in the media library and has the metadata required by every configured criterion.

**Blocked genre**:
A genre whose presence prevents an item from matching the Seerr routing filter, even when the item also has other genres.
_Avoid_: Selected genre, ignored genre

**Original language**:
The language in which a media item was originally produced, not an available audio or subtitle language.
_Avoid_: Audio language, subtitle language

**Release-year range**:
An inclusive optional minimum and maximum release year. An omitted bound leaves that side of the range unrestricted.

**Seerr-routed item**:
An eligible item that matches the Seerr routing filter and is assigned only to Seerr.
_Avoid_: Mirrored item, forwarded subscription


**Target season**:
The TV season named by the Douban item, or the latest regular season when the item does not identify one. Specials are not a target season.

**Handled Seerr request**:
A Seerr request that was newly created or was already represented by an active request or available media. Both outcomes complete processing for the Douban item.

**Retryable Seerr failure**:
A Seerr request attempt that did not create or find a handled request. It remains eligible for another attempt only while the Douban item is inside the configured sync-days window.
