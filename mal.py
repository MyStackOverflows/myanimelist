import requests, ast, warnings, json, qbittorrentapi, time, multiprocessing, pickle
# for some reason the MAL API returns the 'main_picture' field even without asking for it and it
# contains many instances of '\/' which throw a bunch of warnings on screen, so just supress them
warnings.filterwarnings(action="ignore", category=SyntaxWarning)

#################################
# Class Definitions             #
#################################


class MAL:
    ACCESS_TOKEN: str
    REFRESH_TOKEN: str

    def __init__(self, token_file: str) -> None:
        # https://myanimelist.net/apiconfig/references/api/v2 <-- api docs
        # load MAL API tokens -> see refresh_token.py for getting tokens
        with open(token_file) as f:
            data = json.load(f)
            self.ACCESS_TOKEN = data["access_token"]
            self.REFRESH_TOKEN = data["refresh_token"]

    def send_request(self, url: str) -> dict:
        response = requests.get(url, headers={"Authorization": f"Bearer {self.ACCESS_TOKEN}"})
        return ast.literal_eval(response.text)  # converts the json response.text field (essentially a string of response.content) into a python dict

    def get_name(self, json_dict: dict) -> str:
        name = json_dict["alternative_titles"]["en"]
        if name == "":
            name = json_dict["title"]
        return name

    def get_info(self, anime_id: int) -> dict:
        url = f"https://api.myanimelist.net/v2/anime/{anime_id}?fields=id,title,alternative_titles,status,num_episodes,mean"
        json_dict = self.send_request(url)
        return json_dict

    def get_val(self, json_data: dict, key: str) -> str:
        try:
            return json_data[key]
        except KeyError:
            return "n/a"

    def search_mal(self, search: str) -> 'list[Show]':
        out = []
        url = f"https://api.myanimelist.net/v2/anime?q={search}&fields=alternative_titles,anime_id"
        json_dict = self.send_request(url)
        index = 0
        for i in json_dict["data"]:
            show = Show(i["node"]["id"], self)
            print(f"  [{index}] : {show}")
            out.append(show)
            index += 1
        return out


class Show:
    name: str           # name (doh)
    id: int             # MAL id of this show
    is_completed: bool  # is this show finished airing
    length: int         # number of episodes
    rating: float       # MAL score of this show
    NA: str = "n/a"     # class constant for "n/a"

    def __init__(self, id: int, mal: MAL) -> None:
        self.id = id
        json_data = mal.get_info(id)
        self.name = mal.get_name(json_data)
        self.is_completed = json_data["status"] == "finished_airing"
        self.length = mal.get_val(json_data, "num_episodes")
        mean = mal.get_val(json_data, "mean")
        self.rating = -1 if mean == self.NA else float(mean)

    def __str__(self) -> str:
        # https://gist.github.com/egmontkob/eb114294efbcd5adb1944c9f3cb5feda
        # https://stackoverflow.com/questions/22125114/how-to-insert-links-in-python
        # https://stackoverflow.com/a/46289463
        info_string = f"{'✓' if self.is_completed else 'X'} : {self.name} | rated {self.rating if self.rating != -1 else self.NA} | {self.length} episodes"
        url = f"https://myanimelist.net/anime/{self.id}"
        OSC = "\x1b]"   # OSC = operating system command = ESC + ]
        ST = "\x1b\\"   # ST = string terminator = ESC + \
        return f"  {OSC}8;;{url}{ST}{info_string}{OSC}8;;{ST}"

    def __lt__(self, i: 'Show') -> bool:
        return i.rating < self.rating


class Torrent:
    description_url: str    # link to the torrent description page
    file_url: str           # link to the torrent file itself
    seeders: int            # number of seeders
    size: int               # size of the torrent in bytes
    name: str               # name of the torrent

    def __init__(self, qb_dict) -> None:
        self.description_url = qb_dict["descrLink"]
        self.file_url = qb_dict["fileUrl"]
        self.seeders = qb_dict["nbSeeders"]
        self.size = qb_dict["fileSize"]
        self.name = qb_dict["fileName"]

    def __str__(self) -> str:
        size_scaled = float(self.size)
        count = 0
        while size_scaled > 1024:
            size_scaled /= 1024
            count += 1
        size_unit = ""
        match count:
            case 1: size_unit = "KB"
            case 2: size_unit = "MB"
            case 3: size_unit = "GB"
            case 4: size_unit = "TB"
        size_scaled = round(size_scaled, 2)
        return f"\"{self.name}\" | {size_scaled:.2f} {size_unit} | {self.seeders} seeders"

    def __lt__(self, i: 'Torrent') -> bool:
        return i.seeders < self.seeders


class LoadingBar:
    prefix: str     # text to put before the loading bar
    process: multiprocessing.Process    # instance of the internal process

    def __init__(self, prefix: str = "") -> None:
        self.prefix = prefix

    def loading(self) -> None:
        cycle = ["-", "\\", "|", "/"]
        cycle = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        cycle = ["⢎⡰", "⢎⡡", "⢎⡑", "⢎⠱", "⠎⡱", "⢊⡱", "⢌⡱", "⢆⡱"]
        cycle = ["⠋", "⠙", "⠚", "⠞", "⠖", "⠦", "⠴", "⠲", "⠳", "⠓"]
        length = len(cycle)
        index = 0
        while True:
            print(f"\r {self.prefix}{cycle[index % length]}", end="\r", flush=True)
            index += 1
            time.sleep(0.1)

    def start(self) -> None:
        self.process = multiprocessing.Process(target=self.loading)
        self.process.start()

    def stop(self) -> None:
        self.process.kill()
        print(f"\r{' ' * (len(self.prefix) + 5)}", end="\r", flush=True)  # the +5 is just for good measure in case the cycle we're using has >1 width etc
        print(f"{self.prefix}done.")


class Main:
    CACHE_FILE: str
    qb_client: qbittorrentapi.Client
    mal_client: MAL
    commands: dict = {}
    shows: 'list[Show]' = []
    QBITTORRENT: bool = True

    def __init__(self, cache_file: str, client: MAL) -> None:
        self.CACHE_FILE = cache_file
        self.mal_client = client
        self.commands = {"sm": self.cmd_search_mal,
                         "al": self.cmd_add_to_list,
                         "rl": self.cmd_remove_from_list,
                         "sl": self.cmd_search_list,
                         "cl": self.cmd_check_list,
                         "qb": self.cmd_search_qbittorrent,
                         "h": self.cmd_help,
                         "?": self.cmd_help}

        # init qbittorrent api client: https://qbittorrent-api.readthedocs.io/en/latest/
        # requires these environment variables to be set:
        # QBITTORRENTAPI_USERNAME=your_username
        # QBITTORRENTAPI_PASSWORD=your_password
        # QBITTORRENTAPI_HOST=qbittorrent_server_ip:port
        try:
            self.qb_client = qbittorrentapi.Client()
            self.qb_client.auth_log_in()
        except (requests.exceptions.InvalidURL, qbittorrentapi.exceptions.APIConnectionError):
            self.QBITTORRENT = False
            print("qBittorrent server not found, check your environment variables.")
        if not self.QBITTORRENT:
            self.commands["qb"] = lambda: print("qBittorrent server not found, check your environment variables.")

        self.load_list()

    def main(self) -> None:
        while True:
            try:
                self.commands[input(">>> ").lower()]()
            except KeyError:
                print("Invalid command. Type 'h' or '?' to get help.")
            except KeyboardInterrupt:
                self.save_list()
                break

        if self.QBITTORRENT:
            self.qb_client.auth_log_out()    # make sure we log out of our qBittorrent session

    def load_list(self) -> None:
        try:
            with open(self.CACHE_FILE, "rb") as f:
                self.shows += pickle.load(f)     # load cached shows (ie shows that have finished airing)
            print(f"Loaded {len(self.shows)} cached shows.")
        except FileNotFoundError:
            print("No cache file found. If this isn't your first run of the script, make sure you're in the right directory.")

        x = LoadingBar("Refreshing data from myanimelist.net for non 'finished airing' shows... ")
        x.start()
        for i in range(len(self.shows)):
            show = self.shows[i]
            if not show.is_completed:   # if show isn't finished airing, refresh its data
                self.shows[i] = Show(show.id, self.mal_client)
        self.shows = sorted(self.shows)
        x.stop()

    def save_list(self) -> None:
        with open(self.CACHE_FILE, "wb") as f:
            pickle.dump(self.shows, f)   # cache shows
        print(f"\nCached {len(self.shows)} shows.")

    def cmd_search_mal(self) -> None:
        self.mal_client.search_mal(input("Search MAL; enter your search query: "))

    def cmd_add_to_list(self) -> None:
        results = self.mal_client.search_mal(input("Add to list; enter your search query: "))
        index = get_int_input("What index do you want to add? ", True)
        if index != "cancelled":
            show = results[index]
            for i in self.shows:
                if i.id == show.id:
                    print(f"'{show.name}' already in list, cancelling.")
                    return
            self.shows.append(show)
            print(f"Added '{show.name}' to list.")

    def cmd_remove_from_list(self) -> None:
        for i in range(len(self.shows)):
            print(f"  [{i}] : {self.shows[i]}")
        index = get_int_input("What index do you want to remove? ", True)
        if index != "cancelled":
            show = self.shows[index]
            self.shows.pop(index)
            print(f"Removed '{show.name}' from list.")

    def cmd_search_list(self) -> None:
        query = input("Search list; enter your search query: ").lower()
        for show in self.shows:
            if query in str(show).lower():
                print(show)

    def cmd_check_list(self) -> None:
        print('\n'.join([str(i) for i in self.shows]))

    def cmd_search_qbittorrent(self) -> None:
        finished = [show for show in self.shows if show.is_completed]
        for i in range(len(finished)):
            print(f"  [{i}] : {finished[i]}")
        index = get_int_input("What index do you want to search for on qBittorrent? ", True)
        if index != "cancelled":
            show = finished[index]
            query = input("Additional search query (eg `judas`, `batch`, etc): ")
            job = self.qb_client.search_start(f"{show.name}{' ' + query if len(query) > 0 else ''}", "nyaasi", "all")
            x = LoadingBar("Searching with qBittorrent... ")
            x.start()
            while job.status()[0]["status"] == "Running":
                pass
            x.stop()
            torrents = sorted([Torrent(i) for i in job.results()["results"]])    # sort by number of seeders
            for i in range(10 if len(torrents) >= 10 else len(torrents)):
                print(f"  [{i}] : {torrents[i]}")
            index = get_int_input("What index do you want to download with qBittorrent? ", True)
            if index != "cancelled":
                torrent = torrents[index]
                self.qb_client.torrents.add([torrent.file_url])
                print("Torrent added successfully.")

    def cmd_help(self) -> None:
        print("Commands are listed here:" +
              "\n  sm : Search MAL directly" +
              "\n  al : Search MAL and add a show to your List" +
              "\n  rl : Remove a show from your list" +
              "\n  sl : Search your list" +
              "\n  cl : Check status of your list" +
              "\n  qb : Search qBittorrent for torrent links")


def get_int_input(msg: str, cancellable: bool = False) -> int:
    try:
        msgString = f"{msg}{'(or cancel (c)) ' if cancellable else ''}"
        i = input(msgString).lower()
        if cancellable and i == "c":
            return "cancelled"
        x = int(i)
    except ValueError:
        print("Invalid, please enter an integer.")
        x = get_int_input(msg, cancellable)
    return x


if __name__ == "__main__":
    Main("cache.bin", MAL("secret_token.json")).main()
