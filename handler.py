
# 最初の4行はserverless-python-requirements でデプロイパッケージ容量を削減する機能をつかうため
# https://dev.classmethod.jp/cloud/serverless-framework-lambda-numpy-scipy/
try:
    import unzip_requirements
except ImportError:
    pass


import json
import numpy as np
import random

from ask_sdk_core.skill_builder import SkillBuilder
sb = SkillBuilder()
from ask_sdk_core.utils import is_request_type
from ask_sdk_model.ui import SimpleCard
from ask_sdk_model.interfaces.alexa.presentation.apl import RenderDocumentDirective
from ask_sdk_core.utils import is_intent_name

from SPARQLWrapper import SPARQLWrapper
import requests
import urllib
import xml.etree.ElementTree as ET
import os
from urllib.parse import urlparse
import datetime
import re
import locale
import boto3
from botocore.exceptions import ClientError

from logging import getLogger, INFO

logger = getLogger(__name__)
logger.setLevel(INFO)

api_key = os.environ["GOOGLE_MAP_API_KEY"] # 環境変数
url_S3 = os.environ["URL_S3"] # 環境変数

adjective_catchphrase_short = ["お気軽", "とことこ"]
adjective_catchphrase_long = ["がっつり", "気合たっぶり"]
thresh_catch_phrase = 30
destination_catchphrase = ["destinationを目指して", "いざ、destinationへ"]

locale.setlocale(locale.LC_TIME, 'ja_JP.UTF-8') # 曜日を日本語で出力するため

sound_walking = "<audio src='soundbank://soundlibrary/human/amzn_sfx_walking_on_grass_01'/>"
sound_effect = [
    "<audio src='soundbank://soundlibrary/musical/amzn_sfx_trumpet_bugle_01'/>",
    "<audio src='soundbank://soundlibrary/musical/amzn_sfx_drum_comedy_01'/>"
]

max_event_num_mail_send = 4


@sb.request_handler(can_handle_func=is_request_type("LaunchRequest"))
def launch_request_handler(handler_input):
    logger.info("[Intent] LaunchRequest")
    speech_text = "こんにちは。\n神戸市のイベント情報から、散歩コースの提案をします。まずは、散歩したい日付をどうぞ。"

    handler_input = return_response_date_or_minutes(handler_input, speech_text, "left")
    return handler_input.response_builder.response


@sb.request_handler(can_handle_func=is_intent_name("SearchCourseIntent"))
def search_course_intent_handler(handler_input):
    logger.info("[Intent] SearchCourseIntent")

    # # -- <0.1> 日付と歩きたい時間のセット
    result = set_date_and_minutes(handler_input)
    # logger.info("type(result): {}".format(type(result)))
    if type(result) is not list:
        return result # result: handler_input.response_builder.response
    [date_input, date_next_day, minutes_walk_input, speech_text] = result

    # -- <1.1> イベント情報の検索
    event_information = search_event_information(date_input, date_next_day)
    # 既に上で実行済みなので、無駄だが。（上での結果をセッションアトリビュートに保存しておくなどした方が良い）
    for i_event in range(len(event_information)):
        logger.info("event_information[{}]: {}".format(i_event, event_information[i_event]))

    # -- <1.3> 目的地の設定
    [destination, name_event, event_url_end, timef, timet] = set_destination(event_information, date_input)

    # -- <2.1> 目的地周辺の駅の検索
    station = search_station(destination)
    for i in range(len(station)):
        logger.info("station[{}]: {}".format(i, station[i]))
    if len(station) == 0:
        speech_text +=\
            date_input + "の検索がうまく出来ませんでした。すみません。他の日付を言ってみてください。"
        speech_text += "</speak>"
        handler_input.response_builder.speak(speech_text).set_should_end_session(
            False)
        return handler_input.response_builder.response

    # -- <2.2> 目的地から周辺の駅までの所要時間の検索
    time_station2destination = search_time_station2destination(station, destination)
    logger.info("time_station2destination: {}".format(time_station2destination))


    # -- <2.3> ユーザーの「歩きたい時間」に適した駅の組み合わせを設定
    [time_total_result, station_outward, station_homeward] =\
        get_optimal_station_combination(time_station2destination, minutes_walk_input, station)
    logger.info("station: {} to {}".format(station_outward, station_homeward))
    logger.info("time_total_result: {}".format(time_total_result))


    # -- <3> コース名をつける
    name_course = make_course_name(time_total_result, destination, date_input)
    logger.info("name_course: {}".format(name_course))

    # -- <3.2> alexa読み上げテキストを作成
    speech_text = make_speech_text(speech_text, station_outward, station_homeward, name_event, name_course, time_total_result)
    logger.info("speech_text: {}".format(speech_text))

    # -- <3.3> APL表示用のテキスト作成
    weekday_speech = datetime.datetime.strptime(date_input, '%Y-%m-%d').strftime('%A')
    date_mail = date_input[5:7] + "月" + date_input[8:10] + "日（" + weekday_speech[0] + "）"
    apl_title = "神戸の散歩　「" + date_mail + " " + str(minutes_walk_input) + "分」の検索結果"
    apl_url = "https://maps.googleapis.com/maps/api/staticmap" + \
                "?center=" + destination + "&size=600x400" + \
                "&markers=color:blue%7Clabel:1%7C" + "神戸市" + station_outward + \
                "&markers=color:blue%7Clabel:2%7C" + destination + \
                "&markers=color:blue%7Clabel:3%7C" + "神戸市" + station_homeward + "&key=" + api_key
    logger.info("apl_url(map image): {}".format(apl_url))

    apl_name_event = name_event
    apl_name_course = name_course
    apl_station_outward = station_outward[0:-1]
    apl_station_homeward = station_homeward[0:-1]
    apl_time_total_result = str(time_total_result)



    # -- <4> 検索結果をメールする
    speech_text_mail_result = \
        get_address_and_send_mail(handler_input, 
            name_course, name_event, station_outward, station_homeward, time_total_result,
            destination, minutes_walk_input, date_input, event_url_end, timef, timet, event_information
        )
        # 戻り値はメール送信結果
    speech_text += speech_text_mail_result

    speech_text += "では、よい散歩を！またね。"
    speech_text += "</speak>"

    logger.info("mail done ...  speech_text: {}".format(speech_text))

    handler_input = return_response_result(handler_input, speech_text, "right",
        apl_title, apl_url, apl_name_event, apl_name_course, apl_station_outward, apl_station_homeward,
        apl_time_total_result)
    return handler_input.response_builder.response


@sb.request_handler(can_handle_func=is_intent_name("AMAZON.HelpIntent"))
def help_intent_handler(handler_input):
    logger.info("[Intent] AMAZON.HelpIntent")
    speech_text = "神戸市のイベント情報から、散歩コースの提案をします。まずは、散歩したい日付を言って下さい。その次に歩きたい時間を言って下さい。"

    # handler_input.response_builder.speak(speech_text).ask(speech_text).set_card(
    #     SimpleCard("Hello World", speech_text))
    handler_input.response_builder.speak(speech_text).ask(speech_text)
    return handler_input.response_builder.response


@sb.request_handler(
    can_handle_func=lambda input :
        is_intent_name("AMAZON.CancelIntent")(input) or
        is_intent_name("AMAZON.StopIntent")(input))
def cancel_and_stop_intent_handler(handler_input):
    logger.info("[Intent] AMAZON.CancelIntent or StopIntent")
    speech_text = "またね。!"

    # handler_input.response_builder.speak(speech_text).set_card(
    #     SimpleCard("Hello World", speech_text))
    handler_input.response_builder.speak(speech_text)
    return handler_input.response_builder.response


@sb.request_handler(can_handle_func=is_request_type("SessionEndedRequest"))
def session_ended_request_handler(handler_input):
    #any cleanup logic goes here

    return handler_input.response_builder.response


@sb.exception_handler(can_handle_func=lambda i, e: True)
def all_exception_handler(handler_input, exception):
    print("******** all_exception_handler *********")
    # Log the exception in CloudWatch Logs
    print("", str(exception))

    # -- ユーザーに言うように何を促すかを判断する                
    session_attr = handler_input.attributes_manager.session_attributes
    # - session_attrにdateがあるかをチェックする
    slots_date = {
        "date": {
            "name": "date",
            "confirmationStatus": "NONE"
        }
    }
    [status_date, date_raw] = get_param_raw_from_slots_or_session_attr(slots_date, session_attr, "date")
    slots_duration = {
        "duration": {
            "name": "date",
            "confirmationStatus": "NONE"
        }
    }
    [status_duration, duration_raw] = get_param_raw_from_slots_or_session_attr(slots_duration, session_attr, "duration")
    if "OK" not in status_date:
        speech_text = "散歩したい日付を、「来週日曜日」や、「五月五日」<break time='0.1s'/>のように言って下さい。"
    elif "OK" not in status_duration:
        speech_text = "何分くらい散歩したいですか？たとえば、「30分」、「1時間」<break time='0.1s'/>のように言って下さい。"
    else:
        speech_text = "なにかエラーが発生しました。"

    handler_input.response_builder.speak(speech_text).ask(speech_text)
    return handler_input.response_builder.response

handler = sb.lambda_handler()




def _load_apl_document(file_path):
    with open(file_path) as f:
        return json.load(f)


def set_date_and_minutes(handler_input):
    # -- <0.1> 日付と歩きたい時間のセット
    slots = handler_input.request_envelope.request.intent.slots
    session_attr = handler_input.attributes_manager.session_attributes

    # -- 日付のセット
    [status_date, date_raw] = get_param_raw_from_slots_or_session_attr(slots, session_attr, "date")
    logger.info("status_date: {}".format(status_date))
    logger.info("date_raw: {}".format(date_raw))

    # -- 歩きたい時間のセット
    [status_duration, duration_raw] = get_param_raw_from_slots_or_session_attr(slots, session_attr, "duration")
    logger.info("status_duration: {}".format(status_duration))
    logger.info("duration_raw: {}".format(duration_raw))

    speech_text = "<speak>"
    if "OK" in status_date:
        # -- セッションアトリビュートに入れておく
        handler_input.attributes_manager.session_attributes["date"] = date_raw
        if status_date == "OK_slots":
            # -- 日付の入力があったときは、確認のために日付を言う
            # date_speech = date_raw[5:7] + "月" + date_raw[8:10] + "日" #年は言わずに月と日のみにする
            weekday_speech = datetime.datetime.strptime(date_raw, '%Y-%m-%d').strftime('%A')
            date_speech = date_raw[8:10] + "日" + weekday_speech #年月は言わずに日と曜日のみにする
            speech_text += sound_walking + date_speech + "ですね。" + "<break time='0.1s'/>"
    if "OK" in status_duration:
        # -- セッションアトリビュートに入れておく
        handler_input.attributes_manager.session_attributes["duration"] = duration_raw
        if status_duration == "OK_slots":
            # -- 歩きたい時間の入力があったときは、確認のために時間を言う
            minutes_walk_input = get_minutes_from_slot(duration_raw)
            speech_text += sound_walking + str(minutes_walk_input) + "分ですね。" + "<break time='0.1s'/>"

    if "OK" not in status_date:
        # --- NG時のレスポンス
        if status_date == "NG_format":
            speech_text += "散歩したい日付を、「来週日曜日」や、「五月五日」<break time='0.1s'/>のように言って下さい。"
        elif status_date == "NG_event_num":
            weekday_speech = datetime.datetime.strptime(date_raw, '%Y-%m-%d').strftime('%A')
            date_speech = date_raw[5:7] + "月" + date_raw[8:10] + "日" + weekday_speech #月と日と曜日にする
            speech_text += date_speech + "の神戸市のイベント情報はありませんでした。他の日付を言ってみてください。"
        elif status_date == "NG_no_input":
            # 2回目以降の質問となるため、具体例を添えてきく
            speech_text += "散歩したい日付をおしえてください。たとえば、「来週日曜日」、「五月五日」<break time='0.1s'/>のように言って下さい。"
        else:
            speech_text += "日付のところで、なにかエラーが発生しました。"

        speech_text += "</speak>"
        handler_input = return_response_date_or_minutes(handler_input, speech_text, "left")
        return handler_input.response_builder.response

    if "OK" not in status_duration:
        # --- NG時のレスポンス
        if status_duration == "NG_no_input":
            if status_date == "OK_slots":
                # -- 日付の入力があったときは1回目に聞くときなので、簡潔に聞く
                speech_text += "何分くらい散歩したいですか？"
            else:
                # -- 日付の入力がないとき（=時間を聞いたのに入力されなかったとき）、具体例を添えてきく
                speech_text += "何分くらい散歩したいですか？たとえば、「30分」、「1時間」<break time='0.1s'/>のように言って下さい。"
        else:
            speech_text += "散歩したい時間のところで、なにかエラーが発生しました。"

        speech_text += "</speak>"
        handler_input = return_response_date_or_minutes(handler_input, speech_text, "center")
        return handler_input.response_builder.response

    minutes_walk_input = get_minutes_from_slot(duration_raw)
    [date_input, date_next_day] = get_date_from_slot(date_raw)

    return [date_input, date_next_day, minutes_walk_input, speech_text]


def get_param_raw_from_slots_or_session_attr(slots, session_attr, param_name):
    logger.info("<function>  get_param_raw_from_slots_or_session_attr() ({})".format(param_name))
    logger.info("slots: {}".format(slots))
    logger.info("session_attr: {}".format(session_attr))

    param_raw_slot = getattr(slots[param_name], "value", None)
    logger.info("param_raw_slot: {}".format(param_raw_slot))

    if param_raw_slot != None:
        # -- 日付フォーマットに誤りがあればNGを返す
        if param_name == "date":
            regex = re.compile(r'(\d{4})-(\d{2})-(\d{2})')
            mo = regex.search(param_raw_slot)
            if mo == None:
                logger.info("regex.search result: None")
                return ["NG_format", ""] ##[status, param_raw]
            else:
                logger.info("regex.search result: not None (OK)")

        # -- slotにパラメータが入っていて、かつ、形式が問題なければ、その値を使用する
        param_raw = param_raw_slot

        # -- 日付の場合、イベント数を確認してゼロならエラーとする
        if param_name == "date":
            [date_input, date_next_day] = get_date_from_slot(param_raw)
            # -- <1.1> イベント情報の検索
            event_information = search_event_information(date_input, date_next_day)

            # -- <1.2> イベント数が0のときはエラーとする
            num_event = len(event_information)
            logger.info("num_event: {}".format(str(num_event)))
            if num_event == 0:
                return ["NG_event_num", date_input] ##[status, date_raw]

        return ["OK_slots", param_raw] ##[status, param_raw]

    elif param_name in session_attr:
        # -- セッションアトリビュートにパラメータが入っていたら、その値を使用する
        param_raw = session_attr[param_name]
        return ["OK_session_attr", param_raw] ##[status, param_raw]
        
    else:
        param_raw = ""
        return ["NG_no_input", param_raw] ##[status, param_raw]


def get_date_from_slot(date_raw):
    logger.info("<function>  get_date_from_slot()")
    date_input = date_raw
    date_next_day = datetime.datetime.strptime(date_input, '%Y-%m-%d') + datetime.timedelta(days = 1)
    date_next_day = date_next_day.strftime("%Y-%m-%d")
    logger.info("date_input: {}".format(str(date_input)))
    logger.info("date_next_day: {}".format(str(date_next_day)))

    return [date_input, date_next_day]


def get_minutes_from_slot(duration_raw):
    # 単位がhourの場合はをminuteに変換する
    logger.info("<function>  get_minutes_from_slot()")
    duration_value = duration_raw.replace('PT', '').replace('H', '').replace('M', '')
    if "H" in duration_raw:
        minutes_walk_input = int(duration_value) * 60
    elif "M" in duration_raw:
        minutes_walk_input = int(duration_value)
    else:
        logging.error("ERROR:error: neither H nor M included")

    logger.info("minutes_walk_input: {}".format(str(minutes_walk_input)))

    return minutes_walk_input


def search_event_information(date_input, date_next_day):
    logger.info("<function>  search_event_information()")
    sparql = SPARQLWrapper(endpoint='https://data.city.kobe.lg.jp/sparql', returnFormat='json')
    query = """
        PREFIX ic: <http://imi.go.jp/ns/core/rdf#>

        select distinct ?s ?name ?datef ?datet ?timef ?timet ?place where {
        ?s a ic:イベント型.
        ?s ic:名称/ic:表記 ?name .
        ?s ic:期間 [ ic:開始日 ?datef; ic:終了日 ?datet; ic:開始時間 ?timef; ic:終了時間 ?timet ] .
        ?s ic:開催場所/ic:名称/ic:表記 ?place .
        FILTER (xsd:dateTime('""" + date_input + """T00:00:00') <= xsd:dateTime(?datet) && xsd:dateTime(?datet) < xsd:dateTime('""" + date_next_day + """T00:00:00'))
        } LIMIT 100
    """
    sparql.setQuery(query)
    results = sparql.query().convert()
    event_information = results["results"]["bindings"]

    return event_information


def set_destination(event_information, date_input):
    logger.info("<function>  set_destination()")
    num_event = len(event_information)
    logger.info("num_event: {}".format(str(num_event)))
    if num_event == 1:
        index_event = 0
    else:
        # index_event = random.randint(0, num_event-1)
        index_event = int(date_input[8:10]) % num_event #適当に決めたルールでイベントを決める
        logger.info("index_event: {}".format(str(index_event)))

    # for i_event in range(len(event_information)):
    #     logger.info("destination candidates {}: {}".format(i_event, event_information[i_event]["place"]["value"]))

    destination = event_information[index_event]["place"]["value"]   
    name_event = event_information[index_event]["name"]["value"]  
    event_url = event_information[index_event]["s"]["value"]  
    event_url_end = event_url[event_url.find("resource/") + 9:] #resource/以降の部分を抜き出す

    if "神戸" not in destination:
        destination = "神戸市" + destination

    logger.info("destination: {}".format(destination))
    logger.info("name_event: {}".format(name_event))
    logger.info("event_url_end: {}".format(event_url_end))
    
    timef = event_information[index_event]["timef"]["value"]  
    timet = event_information[index_event]["timet"]["value"]  

    logger.info("timef: {}".format(timef))
    logger.info("timet: {}".format(timet))

    return [destination, name_event, event_url_end, timef, timet]


def search_station(name_place):
    # logger.info("<function>  search_station()")
    # logger.info("name_place(after adding '神戸市'): {}".format(name_place))
    ## 場所名から緯度経度を検索する
    [lat, lon] = search_latitude_longitude(name_place)
    # logger.info("lat, lon: {}, {}".format(str(lat), str(lon)))

    ## 緯度経度から近い駅を検索する（SimpleAPI）
    # ref: http://rautaku.hatenablog.com/entry/2018/01/07/153000
    near_station_url = 'http://map.simpleapi.net/stationapi?x={}&y={}&output=xml'.format(lon, lat)
    near_station_req = urllib.request.Request(near_station_url)
    with urllib.request.urlopen(near_station_req) as response:
        near_station_XmlData = response.read()
    near_station_root = ET.fromstring(near_station_XmlData)
    near_station_list = near_station_root.findall(".//name")
    num_station = len(near_station_list)
    station = [""] * num_station
    for i in range(num_station):
        station[i] = near_station_list[i].text
        print("station[i]: ", station[i])
        if station[i] == "市民病院前駅":
            station[i] = "みなとじま" #駅名変更に対応

    return station


# ref: https://qiita.com/komakomako/items/bbfb3636941a98cdb953
def search_latitude_longitude(place):
    logger.info("<function>  search_latitude_longitude()")
    base_url = 'https://maps.googleapis.com/maps/api/geocode/json?language=ja&address={}&key=' + api_key
    headers = {'content-type': 'application/json'}

    place = place.strip()
    url = base_url.format(place)
    logger.info("url: {}".format(url))

    r = requests.get(url, headers=headers)
    data = r.json()
    if 'results' in data and len(data['results']) > 0:
        latitude = data['results'][0]['geometry']['location']['lat']
        longitude = data['results'][0]['geometry']['location']['lng']
    else:
        logger.error("'results' in data and len(data['results']) > 0")
        latitude = 0
        longitude = 0

    return [latitude, longitude] 


def search_time_station2destination(station, destination):
    # logger.info("<function>  search_time_station2destination()")

    num_station = len(station)
    time_station2destination = []
    for i in range(num_station):
        time_station2destination.append(
            search_time_one_station2destination(station[i], destination)
        )

    return time_station2destination


def search_time_one_station2destination(station, destination):
    # logger.info("<function>  search_time_one_station2destination()")

    try:
        api_url =\
            "https://maps.googleapis.com/maps/api/directions/json?origin={0}&destination={1}&mode={2}&language={3}&units={4}&region={5}&key={6}"\
            .format(
                ("神戸市" + station).strip(),
                destination,
                "walking",
                "ja",
                "metric",
                "ja",
                api_key
            )
        # logger.info("api_url: {}".format(api_url))
        p = urlparse(api_url)
        query = urllib.parse.quote_plus(p.query, safe='=&')
        url = '{}://{}{}{}{}{}{}{}{}'.format(
            p.scheme, p.netloc, p.path,
            ';' if p.params else '', p.params,
            '?' if p.query else '', query,
            '#' if p.fragment else '', p.fragment)
        html = urllib.request.urlopen(url)
        html_json = json.loads(html.read().decode('utf-8'))
        time_station2destination = int(html_json["routes"][0]["legs"][0]["duration"]["value"] / 60) #[min]

        return time_station2destination

    except Exception as e:
        raise e


def get_optimal_station_combination(time_station2destination, minutes_walk_input, station):
    logger.info("<function>  get_optimal_station_combination()")
    # -- 駅の各組み合わせでの徒歩時間合計を計算し、行列にする
    num_time = len(time_station2destination)
    time_total = [[0] * num_time for i in range(num_time)]
    for i in range(num_time):
        for j in range(num_time):
            time_total[i][j] = time_station2destination[i] + time_station2destination[j]

    error = np.array(time_total) - np.full_like(time_total, minutes_walk_input)     # ユーザー入力時間（分）との誤差
    index_min_station = np.unravel_index(np.argmin(np.abs(error)), error.shape)     # 誤差最小となるインデックス
    time_total_result = time_station2destination[index_min_station[0]] + time_station2destination[index_min_station[1]]
    station_outward = station[index_min_station[0]]     # 行きの駅
    station_homeward = station[index_min_station[1]]    # 帰りの駅

    logger.info("time_total: {}".format(time_total))
    logger.info("error: {}".format(error))
    logger.info("index_min_station: {}".format(index_min_station))

    return [time_total_result, station_outward, station_homeward]


def make_course_name(time_total_result, destination, date_input):
    # logger.info("<function>  make_course_name()")
    # -- 形容詞（がっつりコース、など）
    # 徒歩時間の長さに応じて決める
    if time_total_result > thresh_catch_phrase: 
        index_adjective = int(date_input[8:10]) % len(adjective_catchphrase_long) #ランダム
        adjective_phrase = adjective_catchphrase_long[index_adjective]
    else:
        index_adjective = int(date_input[8:10]) % len(adjective_catchphrase_short)
        adjective_phrase = adjective_catchphrase_short[index_adjective]

    # -- 目的地（○○を目指して、など）
    index_destination = int(date_input[8:10]) % len(destination_catchphrase)
    destination_phrase = destination_catchphrase[index_destination].replace("destination", destination)

    name_course = destination_phrase + "、" + adjective_phrase + "コース"

    return name_course


def send_mail_ses(recipient, mail_text):
    logger.info("<function>  send_mail_ses()")
    SENDER = "Kobe Sampo [Alexa Skill] <kobe_sampo@r-ikechan.com>"
    AWS_REGION = "us-east-1"
    SUBJECT = "神戸の散歩 ルート検索結果（Alexaスキル）"

    CHARSET = "UTF-8"
    client = boto3.client('ses',region_name=AWS_REGION)

    try:
        response = client.send_email(
            Destination={
                'ToAddresses': [
                    recipient,
                ],
            },
            Message={
                'Body': {
                    'Text': {
                        'Charset': CHARSET,
                        'Data': mail_text,
                    },
                },
                'Subject': {
                    'Charset': CHARSET,
                    'Data': SUBJECT,
                },
            },
            Source=SENDER,
        )
    except ClientError as e:
        logger.error(e.response['Error']['Message'])
    else:
        logger.info("Email sent! Message ID: {}".format(response['MessageId']))


def make_mail_text_event(name_course, name_event, station_outward, station_homeward, time_total_result,
    destination, minutes_walk_input, date_input, event_url_end, timef, timet):

    # - urlを作成する
    # -- Google MapのURLに使用できない日本語を変換する
    destination_url = destination.replace(" ", "").replace("　", "") #スペースを削除
    if "（" in destination_url and "）" in destination_url:
        destination_url = destination_url[0 : destination_url.find("（")] + " " + destination_url[destination_url.find("）")+3:] #（、）の間を削除
    if "「" in destination_url and "」" in destination_url:
        destination_url = destination_url[0 : destination_url.find("「")] + " " + destination_url[destination_url.find("」")+3:] #「、」の間を削除
    if "『" in destination_url and "』" in destination_url:
        destination_url = destination_url[0 : destination_url.find("『")] + " " + destination_url[destination_url.find("』")+3:] #『、』の間を削除
    if "【" in destination_url and "】" in destination_url:
        destination_url = destination_url[0 : destination_url.find("【")] + " " + destination_url[destination_url.find("】")+3:] #【、】の間を削除
    url = "https://www.google.com/maps/dir/?api=1&origin=" + "神戸市" + station_outward + \
        "&destination=" + "神戸市" + station_homeward + \
        "&travelmode=walking&waypoints=" + destination_url

    name_course_mail = name_course.replace("　", " ")
    name_event_mail = name_event.replace("　", " ")

    mail_text_event = (
        "『" + name_course_mail + "』" + "\n" +
        "イベント名\t" + name_event_mail + 
        "（https://data.city.kobe.lg.jp/eventdata/event/" + event_url_end + "）\n" +
        "\t\t" + timef[0:5] + "〜" + timet[0:5] + "\n" +
        "行きの駅\t" + station_outward + "\n" +
        "帰りの駅\t" + station_homeward + "\n" +
        "徒歩時間合計\t" + str(time_total_result) + "分" + "\n" +
        "ルート検索結果（Google Map）：　" + "\n" +
        url + "\n" +
        "　\n"
    )

    return mail_text_event


def set_other_destination(event_information, date_input, event_url_end_alexa_read):
    logger.info("<function>  set_other_destination()")
    num_event = len(event_information)
    if num_event == 1:
        index_event = 0
        logger.info("no other event")
        return [[],[],[],[],[]] #return null list

    index_event_read = int(date_input[8:10]) % num_event #alexaから読み上げたイベントのインデックス
    index_list = list(range(num_event))
    index_list.remove(index_event_read) #読み上げ済みを除いた、イベントのインデックス

    if len(index_list) > max_event_num_mail_send:
        index_list = index_list[:max_event_num_mail_send] #イベント数は上限を設定（タイムアウト対策）

    destination_list = []
    name_event_list = []
    event_url_end_list = []
    timef_list = []
    timet_list = []
    for i in index_list:
        event_url = event_information[i]["s"]["value"]  
        event_url_end = event_url[event_url.find("resource/") + 9:] #resource/以降の部分を抜き出す
        event_url_end_list_including_alexa_read = event_url_end_list.copy()
        event_url_end_list_including_alexa_read.append(event_url_end_alexa_read)
        if event_url_end not in event_url_end_list_including_alexa_read:
            # ----- 重複するURL末尾がないときのみ（時刻違いで同じイベントが複数ある場合は、省くようにする）
            # （Alexaから既に読み上げたものとの重複もチェックする）
            destination = event_information[i]["place"]["value"]
            if "神戸" not in destination:
                destination = "神戸市" + destination
            destination_list.append(destination)
            name_event_list.append(event_information[i]["name"]["value"])
            timef_list.append(event_information[i]["timef"]["value"])
            timet_list.append(event_information[i]["timet"]["value"])
            event_url_end_list.append(event_url_end)

    logger.info("destination_list: {}".format(destination_list))
    logger.info("name_event_list: {}".format(name_event_list))
    logger.info("event_url_end_list: {}".format(event_url_end_list))
    
    return [destination_list, name_event_list, event_url_end_list, timef_list, timet_list]


def make_speech_text(speech_text, station_outward, station_homeward, name_event, name_course, time_total_result):
    sound_effect_play = sound_effect[random.randint(0,len(sound_effect)-1)] #ランダムに決める

    # 駅が行きと帰りで同じかどうかによって、助詞を切り替える
    if station_outward == station_homeward: 
        postpositional = "も"
    else:
        postpositional = "は"

    speech_text += \
        "それでは、「" + name_event + "」というイベントに行ってみてはいかがでしょう。<break time='0.5s'/>" +\
        '<prosody pitch="+10%" volume="loud">' +\
        "名付けて、"+ sound_effect_play + "「" + name_course + "」！<break time='0.5s'/>" +\
        '</prosody>' +\
        "行きは" + station_outward + "、帰り" + postpositional + station_homeward + "を使うと、徒歩時間は" + str(time_total_result) + "分になります。" + "<break time='0.8s'/>"
    
    return speech_text


def get_address_and_send_mail(handler_input,  
        name_course, name_event, station_outward, station_homeward, time_total_result,
        destination, minutes_walk_input, date_input, event_url_end, timef, timet, event_information):
    logger.info("<function>  get_address_and_send_mail()")

    # - メールアドレスの取得
    endpoint = handler_input.request_envelope.context.system.api_endpoint
    url = endpoint + "/v2/accounts/~current/settings/Profile.email"
    accessToken = handler_input.request_envelope.context.system.api_access_token
    headers = {
        'Accept': 'application/json',
        "Authorization": "Bearer " + accessToken
    }
    r = requests.get(url, headers=headers)
    logger.info("get_mail_address status_code: {}".format(str(r.status_code)))

    if r.status_code == 403:
        # - 許可されていないときの処理
        logger.error("unauthorized")
        speech_text_mail_result = "検索結果をメールで送る機能もあります。Alexaアプリで、スキルに権限を設定してください。"

    elif r.status_code == 200:
        # - メールアドレスを正常取得できたときの処理
        recipient = r.json() #mail address
        # logger.info("mail_address: {}".format(recipient)) ########## test only

        # -- 検索条件のメール本文を作成
        mail_text_condition = make_mail_text_condition(date_input, minutes_walk_input)
        # logger.info("mail_text_condition: {}".format(mail_text_condition)) 
        
        # -- イベント情報＆検索結果のメール本文を作成　alexaから読み上げするイベントのみ
        mail_text_event_1st = make_mail_text_event(
            name_course, name_event, station_outward, station_homeward, time_total_result,
            destination, minutes_walk_input, date_input, event_url_end, timef, timet
        )

        mail_text = mail_text_condition + mail_text_event_1st


        # -- イベント情報＆検索結果をメール本文に追加　alexa読み上げ分以外
        [destination_list, name_event_list, event_url_end_list, timet_list, timef_list] =\
            set_other_destination(event_information, date_input, event_url_end)
        if len(destination_list) != 0:
            mail_text += (
                "　\n" +
                "他のイベント情報" +
                "　\n" +
                "　\n"
            )
        for i in range(len(destination_list)):
            destination = destination_list[i]
            name_event = name_event_list[i]
            event_url_end = event_url_end_list[i]
            timet = timet_list[i]
            timef = timef_list[i]

            # -- <2.1> 目的地周辺の駅の検索
            station = search_station(destination)
            if len(station) == 0:
                continue

            # -- <2.2> 目的地から周辺の駅までの所要時間の検索
            time_station2destination = search_time_station2destination(station, destination)

            # -- <2.3> ユーザーの「歩きたい時間」に適した駅の組み合わせを設定
            [time_total_result, station_outward, station_homeward] =\
                get_optimal_station_combination(time_station2destination, minutes_walk_input, station)

            # -- <3> コース名をつける
            name_course = make_course_name(time_total_result, destination, date_input)

            mail_text += make_mail_text_event(name_course, name_event, station_outward, station_homeward, time_total_result, destination, minutes_walk_input, date_input, event_url_end, timef, timet)

        send_mail_ses(recipient, mail_text)

        speech_text_mail_result = "検索結果をメールで送りました。"

    else:
        logger.error("cannot get mail_address")
        speech_text_mail_result = "エラーのため、検索結果のメール送信は出来ませんでした。"

    return speech_text_mail_result


def make_mail_text_condition(date_input, minutes_walk_input):
    weekday_speech = datetime.datetime.strptime(date_input, '%Y-%m-%d').strftime('%A')
    # logger.info("weekday_speech: {}".format(weekday_speech)) 
    date_mail = date_input[5:7] + "月" + date_input[8:10] + "日（" + weekday_speech[0] + "）"
    # logger.info("date_mail: {}".format(date_mail)) 
    mail_text_condition = "「" + date_mail + "に" + str(minutes_walk_input) + "分歩きたい」の検索結果" + "\n" + "　\n"

    return mail_text_condition


def return_response_date_or_minutes(handler_input, speech_text, person_align):
    logger.info("<function>  return_response_date_or_minutes()")
    is_apl_supported = \
        handler_input.request_envelope.context.system.device.supported_interfaces.alexa_presentation_apl
    logger.info("is_apl_supported: {}".format(is_apl_supported))

    if is_apl_supported:
        handler_input.response_builder.speak(speech_text)\
            .set_card(SimpleCard("神戸の散歩", speech_text))\
            .set_should_end_session(False)\
            .add_directive(
                RenderDocumentDirective(
                    document=_load_apl_document("./apl_template/apl_template_date_or_minutes.json"),
                    datasources= {
                        "bodyTemplate2Data": {
                            "type": "object",
                            "objectId": "bt2Sample",
                            "title": "神戸の散歩",
                            "textContent": {
                                "prompt": {
                                    "type": "PlainText",
                                    "text": speech_text
                                }
                            },
                            "logoUrl": url_S3 + "/logo.png",
                            "personUrl": url_S3 + "/walking_man_right.png",
                            "personAlign": person_align
                        }
                    }
                )
            )
    else:
        handler_input.response_builder.speak(speech_text)\
            .set_card(SimpleCard("神戸の散歩", speech_text))\
            .set_should_end_session(False)

    logger.info("[Prompt] {}".format(speech_text))

    return handler_input


def return_response_result(handler_input, speech_text, person_align, 
    apl_title, apl_url, apl_name_event, apl_name_course, apl_station_outward, apl_station_homeward,
    apl_time_total_result):
    logger.info("<function>  return_response_result()")
    is_apl_supported = \
        handler_input.request_envelope.context.system.device.supported_interfaces.alexa_presentation_apl
    logger.info("is_apl_supported: {}".format(is_apl_supported))

    if is_apl_supported:
        handler_input.response_builder.speak(speech_text)\
            .set_card(SimpleCard("神戸の散歩", speech_text))\
            .set_should_end_session(True)\
            .add_directive(
                RenderDocumentDirective(
                    document=_load_apl_document("./apl_template/apl_template_result.json"),
                    datasources= {
                        "bodyTemplate2Data": {
                            "type": "object",
                            "objectId": "bt2Sample",
                            "title": apl_title,
                            "image": {
                                "sources": [    
                                    {
                                        "url": apl_url,
                                        "size": "large",
                                        "widthPixels": 0,
                                        "heightPixels": 0
                                    }
                                ]
                            },
                            "textContent": {
                                "event": {
                                    "type": "PlainText",
                                    "text": "イベント：<br>" + apl_name_event
                                },
                                "name_course": {
                                    "type": "PlainText",
                                    "text": apl_name_course
                                },
                                "station_outward": {
                                    "type": "PlainText",
                                    "text": "行きの駅：　" + apl_station_outward
                                },
                                "station_homeward": {
                                    "type": "PlainText",
                                    "text": "帰りの駅：　" + apl_station_homeward
                                },
                                "time_total_result": {
                                    "type": "PlainText",
                                    "text": "徒歩時間合計：　" + apl_time_total_result + "分"
                                }
                            },
                            "logoUrl": url_S3 + "/logo.png",
                            "personUrl": url_S3 + "/walking_man_right.png"
                        }
                    }
                )
            )
    else:
        handler_input.response_builder.speak(speech_text)\
            .set_card(SimpleCard("神戸の散歩", speech_text))\
            .set_should_end_session(True)

    logger.info("[Prompt] {}".format(speech_text))

    return handler_input