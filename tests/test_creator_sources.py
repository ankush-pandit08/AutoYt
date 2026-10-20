from types import SimpleNamespace
import httplib2
from googleapiclient.errors import HttpError
from autoyt import youtube_content, youtube
from test_youtube_content import item


def result(items):return SimpleNamespace(execute=lambda:{"items":items})


def test_followed_creators_use_upload_playlists_and_metadata_batches():
    calls=[]
    class Client:
        def channels(self):return SimpleNamespace(list=self.channels_list)
        def channels_list(self,**kw):
            calls.append(kw)
            return result([{"contentDetails":{"relatedPlaylists":{"uploads":"playlist-one"}}},
                           {"contentDetails":{"relatedPlaylists":{"uploads":"playlist-two"}}}])
        def playlistItems(self):return SimpleNamespace(list=self.playlist_list)
        def playlist_list(self,**kw):
            if kw["playlistId"]=="playlist-two":raise HttpError(httplib2.Response({"status":"404"}),b"{}")
            return result([{"contentDetails":{"videoId":"abc123DEF45"}}])
        def videos(self):return SimpleNamespace(list=lambda **kw:result([item()]))
    found=youtube_content.followed_creator_items(Client(),["creator-one","creator-two","creator-one"])
    assert len(found)==1 and found[0]["id"]=="abc123DEF45"
    assert calls[0]["id"]=="creator-one,creator-two"


def test_followed_ai_topics_rank_higher_but_old_and_nontech_are_not_news(cfg):
    cfg.creator_channel_ids=["creator-channel"];cfg.topic_focus="ai_coding"
    followed=item(title="New Claude AI model for coding")
    generic=item("xyz123DEF45",title="Latest phone technology")
    generic["snippet"]["channelId"]="other-channel";generic["snippet"]["description"]="Phone technology"
    old=item("old123DEF45",age=8)
    unrelated=item("non123DEF45",title="Celebrity wedding")
    unrelated["snippet"]["description"]="Wedding celebrations"
    topics=youtube_content.topics_from_items([generic,followed,old,unrelated],cfg)
    assert len(topics)==2 and topics[0].url.endswith("abc123DEF45")
    assert topics[0].youtube_metadata["signal"]=="followed_creator"
    assert topics[0].creator_references[0]["source"].endswith("creator-channel")


def test_change_of_creator_configuration_invalidates_discovery_cache(cfg,monkeypatch):
    calls=[]
    class Client:
        def videos(self):return self
        def search(self):return self
        def list(self,**kw):
            if "type" in kw:return result([])
            return result([item()])
    monkeypatch.setattr(youtube,"service",lambda c:(Client(),None))
    monkeypatch.setattr(youtube_content,"followed_creator_items",lambda client,ids:calls.append(list(ids)) or [])
    youtube_content.discover(cfg);youtube_content.discover(cfg)
    assert len(calls)==1
    cfg.creator_channel_ids=["new-source"]
    youtube_content.discover(cfg)
    assert calls==[[],["new-source"]]
