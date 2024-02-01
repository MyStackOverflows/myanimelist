import requests, ast, warnings, json, qbittorrentapi, time, multiprocessing, pickle, os
# for some reason the MAL API returns the 'main_picture' field even without asking for it and it
# contains many instances of '\/' which throw a bunch of warnings on screen, so just supress them
warnings.filterwarnings(action="ignore", category=SyntaxWarning)

#################################
# Class Definitions             #
#################################


class Show:
    name: str           # name (doh)
    id: int             # MAL id of this show
    is_completed: bool  # is this show finished airing
    length: int         # number of episodes
    rating: float       # MAL score of this show
    NA: str = "n/a"     # class constant for "n/a"

    def __init__(self, id: int) -> None:
        self.id = id
        json_data = get_info(id)
        self.name = get_name(json_data)
        self.is_completed = json_data["status"] == "finished_airing"
        self.length = get_val(json_data, "num_episodes")
        mean = get_val(json_data, "mean")
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
    LIST_FILE: str
    qb_client: qbittorrentapi.Client
    commands: dict = {}
    shows: 'list[Show]' = []
    QBITTORRENT: bool = True

    def __init__(self, cache_file: str, list_file: str):
        self.CACHE_FILE = cache_file
        self.LIST_FILE = list_file
        self.commands = {"c": self.cmd_check_status,
                         "a": self.cmd_add_to_list,
                         "r": self.cmd_remove_from_list,
                         "s": self.cmd_search_list,
                         "cl": self.cmd_check_list,
                         "q": self.cmd_search_qbittorrent,
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
            self.commands["q"] = lambda: print("qBittorrent server not found, check your environment variables.")

    def main(self):
        self.load_list()
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
        id_list = []
        if not os.path.exists(self.LIST_FILE):
            open(self.LIST_FILE, "w").close()
            return
        for line in open(self.LIST_FILE, mode="r").readlines():
            ids = line.split(",")
            while "" in ids:
                ids.remove("")
            while "\n" in ids:
                ids.remove("\n")
            id_list += [int(i) for i in ids]

        try:
            with open(self.CACHE_FILE, "rb") as f:
                self.shows += pickle.load(f)     # load cached shows (ie shows that have finished airing)
            print(f"Loaded {len(self.shows)} cached shows.")
        except FileNotFoundError:
            print("No cache file found. If this isn't your first run of the script, make sure you're in the right directory.")

        x = LoadingBar("Loading data from myanimelist.net for uncached shows... ")
        x.start()
        for id in id_list:
            cached = bool(len([show for show in self.shows if show.id == id]))
            if not cached:  # minimize our calls to MAL API
                self.shows.append(Show(id))
        x.stop()

    def save_list(self) -> None:
        with open(self.LIST_FILE, "w") as f:
            f.write(",".join([str(i.id) for i in self.shows]))
        print("\nList saved.")
        shows_to_cache = [show for show in self.shows if show.is_completed]
        with open(self.CACHE_FILE, "wb") as f:
            pickle.dump(shows_to_cache, f)   # cache shows that have finished airing
        print(f"Cached 'finished airing' shows ({len(shows_to_cache)}/{len(self.shows)} total shows).")

    def cmd_check_status(self) -> None:
        results = search_mal(input("Check status; enter your search query: "))
        index = get_int_input("What index do you want to check? ")
        print(Show(results[index]))

    def cmd_add_to_list(self) -> None:
        results = search_mal(input("Add to list; enter your search query: "))
        index = get_int_input("What index do you want to add? ", True)
        if index != "cancelled":
            self.shows.append(Show(results[index]))

    def cmd_remove_from_list(self) -> None:
        for i in range(len(self.shows)):
            print(f"  [{i}] : {self.shows[i]}")
        index = get_int_input("What index do you want to remove? ", True)
        if index != "cancelled":
            self.shows.pop(index)

    def cmd_search_list(self) -> None:
        query = input("Search list; enter your search query: ").lower()
        for show in self.shows:
            if query in str(show).lower():
                print(show)

    def cmd_check_list(self) -> None:
        print('\n'.join([str(i) for i in sorted(self.shows)]))

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
        print("Commands are (c/a/r/s/cl/q) and listed here:\n - Search and check an invidiual show's status: c\n - Search and add a show to your list: a\n - Remove a show from your list: r\n - Search your list: s\n - Check your whole list's status: cl\n - Search qBittorrent for torrent links: q")


#################################
# Utility Methods               #
#################################


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


def send_request(url: str):
    response = requests.get(url, headers={"Authorization": f"Bearer {ACCESS_TOKEN}"})
    return ast.literal_eval(response.text)  # converts the json response.text field (essentially a string of response.content) into a python dict


def get_name(json_dict: dict):
    name = json_dict["alternative_titles"]["en"]
    if name == "":
        name = json_dict["title"]
    return name


def get_info(anime_id: int):
    url = f"https://api.myanimelist.net/v2/anime/{anime_id}?fields=id,title,alternative_titles,status,num_episodes,mean"
    json_dict = send_request(url)
    return json_dict


def get_val(json_data: str, key: str):
    try:
        return json_data[key]
    except KeyError:
        return "n/a"


def search_mal(search: str) -> []:
    ids = []
    url = f"https://api.myanimelist.net/v2/anime?q={search}&fields=alternative_titles,anime_id"
    json_dict = send_request(url)
    index = 0
    for i in json_dict["data"]:
        id = i["node"]["id"]
        id_dict = send_request(f"https://api.myanimelist.net/v2/anime/{id}?fields=alternative_titles")
        print(f"  [{index}] : {get_name(id_dict)}")
        ids.append(id)
        index += 1
    return ids


#################################
# Main logic                    #
#################################

if __name__ == "__main__":
    # https://myanimelist.net/apiconfig/references/api/v2 <-- api docs
    # https://myanimelist.net/apiconfig/references/authorization <-- followed these steps to get ACCESS_TOKEN and REFRESH_TOKEN
    # load MAL API tokens
    with open("secret_token.json") as f:
        data = json.load(f)
        ACCESS_TOKEN = data["access_token"]
        REFRESH_TOKEN = data["refresh_token"]

    Main("cache.bin", "list.txt").main()
