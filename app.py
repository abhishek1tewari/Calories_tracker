import streamlit as st
import requests
import pandas as pd
import os
import json
import time
from datetime import datetime, timedelta

# ----------------------------------
# PAGE CONFIGURATION
# ----------------------------------

st.set_page_config(
    page_title="My Calorie Tracker",
    page_icon="🥗",
    layout="wide"
)

FOOD_FILE = "calorie_tracker.csv"
SETTINGS_FILE = "settings.json"

USDA_API_URL = (
    "https://api.nal.usda.gov/fdc/v1/foods/search"
)

COLUMNS = [
    "id",
    "food_name",
    "calories",
    "quantity",
    "date",
    "quantity_type",
    "unit_count",
    "unit_weight"
]


# ----------------------------------
# CSV STORAGE SETUP
# ----------------------------------

def read_food_data():

    if not os.path.exists(FOOD_FILE):

        return pd.DataFrame(columns=COLUMNS)

    try:

        df = pd.read_csv(FOOD_FILE)

        for column in COLUMNS:

            if column not in df.columns:

                df[column] = 0

        return df[COLUMNS]

    except (pd.errors.EmptyDataError, pd.errors.ParserError):

        return pd.DataFrame(columns=COLUMNS)


def save_food_data(df):

    df.to_csv(
        FOOD_FILE,
        index=False
    )


def create_database():

    # Create food CSV file

    if not os.path.exists(FOOD_FILE):

        df = pd.DataFrame(columns=COLUMNS)

        save_food_data(df)

    # Create settings JSON file

    if not os.path.exists(SETTINGS_FILE):

        settings = {
            "daily_target": 2000
        }

        with open(SETTINGS_FILE, "w") as f:

            json.dump(
                settings,
                f,
                indent=4
            )

    delete_old_data()


def delete_old_data():

    df = read_food_data()

    if df.empty:
        return

    cutoff = (
        datetime.now() - timedelta(days=10)
    )

    dates = pd.to_datetime(
        df["date"],
        errors="coerce"
    )

    df = df[
        (dates >= cutoff) & dates.notna()
    ]

    save_food_data(df)


# ----------------------------------
# USDA FOODDATA CENTRAL API
# ----------------------------------

def get_calories_from_nutrients(nutrients):

    kcal = None
    kj = None

    for nutrient in nutrients:

        nutrient_id = nutrient.get("nutrientId")

        nutrient_info = nutrient.get(
            "nutrient",
            {}
        )

        if nutrient_id is None:

            nutrient_id = nutrient.get(
                "nutrientNumber"
            )

        if nutrient_id is None:

            nutrient_id = nutrient_info.get("id")

        nutrient_name = (
            nutrient.get("nutrientName")
            or nutrient_info.get("name")
            or ""
        ).lower()

        unit = (
            nutrient.get("unitName")
            or nutrient_info.get("unitName")
            or ""
        ).lower()

        value = (
            nutrient.get("value")
            if nutrient.get("value") is not None
            else nutrient.get("amount")
        )

        try:

            value = float(value)

        except (ValueError, TypeError):

            continue

        if value < 0:
            continue

        # Energy in kcal

        if unit == "kcal" and (
            nutrient_id in (1008, 2047, 2048)
            or "energy" in nutrient_name
        ):

            if nutrient_id == 1008:

                kcal = value
                break

            if kcal is None:

                kcal = value

        # Energy in kJ

        elif unit == "kj" and (
            nutrient_id == 1062
            or "energy" in nutrient_name
        ):

            kj = value

    if kcal is not None:

        return kcal

    if kj is not None:

        return kj / 4.184

    return None


@st.cache_data(
    ttl=60,
    show_spinner=False
)
def search_food_api(food_name, api_key):

    params = {

        "query": food_name,

        "pageSize": 20,

        "pageNumber": 1,

        "api_key": api_key

    }

    for attempt in range(3):

        try:

            response = requests.get(

                USDA_API_URL,

                params=params,

                timeout=20

            )

            if response.status_code == 429:

                if attempt < 2:

                    time.sleep(2 ** attempt)

                    continue

                return [], (
                    "USDA API rate limit exceeded. "
                    "Please try again later."
                )

            response.raise_for_status()

            data = response.json()

            results = []

            for product in data.get("foods", []):

                nutrients = product.get(
                    "foodNutrients",
                    []
                )

                calories = get_calories_from_nutrients(
                    nutrients
                )

                if calories is None:
                    continue

                if calories < 0:
                    continue

                name = (
                    product.get("description")
                    or ""
                ).strip()

                if not name:
                    continue

                # ----------------------------------
                # SERVING INFORMATION
                # ----------------------------------

                serving_weight = None

                serving_size = ""

                api_serving_size = product.get(
                    "servingSize"
                )

                serving_unit = product.get(
                    "servingSizeUnit",
                    ""
                )

                if api_serving_size is not None:

                    try:

                        api_serving_size = float(
                            api_serving_size
                        )

                        serving_size = (
                            f"{api_serving_size:g} "
                            f"{serving_unit}"
                        )

                        if (
                            serving_unit.lower() == "g"
                            and api_serving_size > 0
                        ):

                            serving_weight = api_serving_size

                    except (ValueError, TypeError):

                        pass

                # Get weight from food portions

                portions = product.get(
                    "foodPortions",
                    []
                )

                if serving_weight is None and portions:

                    first_portion = portions[0]

                    gram_weight = first_portion.get(
                        "gramWeight"
                    )

                    try:

                        gram_weight = float(
                            gram_weight
                        )

                        if gram_weight > 0:

                            serving_weight = gram_weight

                    except (ValueError, TypeError):

                        pass

                    if not serving_size:

                        serving_size = (
                            first_portion.get("modifier")
                            or ""
                        )

                brand = (
                    product.get("brandOwner")
                    or product.get("brandName")
                    or ""
                )

                results.append({

                    "name": name,

                    "calories": calories,

                    "brand": brand,

                    "serving_weight": serving_weight,

                    "serving_size": serving_size,

                    "barcode": str(
                        product.get("gtinUpc")
                        or product.get("fdcId", "")
                    )

                })

            return results, None

        except requests.exceptions.HTTPError as e:

            status = (
                e.response.status_code
                if e.response is not None
                else None
            )

            if status in (401, 403):

                return [], (
                    "Invalid USDA API key. "
                    "Please check your API key."
                )

            return [], f"USDA API error: {e}"

        except requests.exceptions.RequestException as e:

            if attempt < 2:

                time.sleep(2 ** attempt)

            else:

                return [], f"Connection error: {e}"

        except ValueError:

            return [], (
                "Invalid response received from USDA API."
            )

    return [], "Unable to retrieve food information."


# ----------------------------------
# ADD FOOD
# ----------------------------------

def add_food(
    food,
    quantity,
    quantity_type,
    unit_count=0,
    unit_weight=0
):

    if quantity_type == "Grams (g)":

        total_weight = quantity

    else:

        total_weight = unit_count * unit_weight

    calories = (
        food["calories"] * total_weight / 100
    )

    df = read_food_data()

    existing_ids = pd.to_numeric(
        df["id"],
        errors="coerce"
    ).dropna()

    if existing_ids.empty:

        next_id = 1

    else:

        next_id = int(
            existing_ids.max()
        ) + 1

    new_entry = pd.DataFrame([{

        "id": next_id,

        "food_name": food["name"],

        "calories": calories,

        "quantity": total_weight,

        "date": datetime.now().isoformat(
            timespec="seconds"
        ),

        "quantity_type": quantity_type,

        "unit_count": unit_count,

        "unit_weight": unit_weight

    }])

    df = pd.concat(
        [df, new_entry],
        ignore_index=True
    )

    save_food_data(df)


# ----------------------------------
# GET FOOD DIARY
# ----------------------------------

def get_daily_food():

    df = read_food_data()

    if df.empty:
        return []

    today = datetime.now().date()

    dates = pd.to_datetime(
        df["date"],
        errors="coerce"
    )

    df = df[
        dates.dt.date == today
    ]

    df = df.sort_values(
        "id",
        ascending=False
    )

    return df[COLUMNS].values.tolist()


def get_daily_total():

    entries = get_daily_food()

    return sum(
        float(row[2])
        for row in entries
    )


def delete_food(food_id):

    df = read_food_data()

    ids = pd.to_numeric(
        df["id"],
        errors="coerce"
    )

    df = df[
        ids != food_id
    ]

    save_food_data(df)


# ----------------------------------
# SETTINGS
# ----------------------------------

def get_target():

    try:

        with open(SETTINGS_FILE, "r") as f:

            settings = json.load(f)

        return settings.get(
            "daily_target",
            2000
        )

    except (FileNotFoundError, json.JSONDecodeError):

        return 2000


def update_target(target):

    settings = {

        "daily_target": target

    }

    with open(SETTINGS_FILE, "w") as f:

        json.dump(
            settings,
            f,
            indent=4
        )


# ----------------------------------
# HISTORY
# ----------------------------------

def get_history():

    df = read_food_data()

    if df.empty:
        return []

    df["date"] = pd.to_datetime(
        df["date"],
        errors="coerce"
    )

    df = df.dropna(
        subset=["date"]
    )

    df["day"] = df["date"].dt.date

    history = (
        df.groupby("day")["calories"]
        .sum()
        .sort_index(ascending=False)
        .head(10)
    )

    return [
        (str(day), calories)
        for day, calories in history.items()
    ]


# ----------------------------------
# INITIALIZE APPLICATION
# ----------------------------------

create_database()


# ----------------------------------
# SIDEBAR - USDA API KEY
# ----------------------------------

st.sidebar.title("⚙️ Settings")

st.sidebar.subheader(
    "USDA API Configuration"
)

st.sidebar.caption(
    "Enter your free USDA FoodData Central API key."
)

api_key = st.sidebar.text_input(

    "USDA API Key",

    type="password",

    placeholder="Enter your API key",

    help="Your API key is kept in the current session."

)

if api_key:

    st.sidebar.success(
        "API key entered."
    )

else:

    st.sidebar.warning(
        "Enter your USDA API key to search foods."
    )

st.sidebar.markdown("---")

st.sidebar.caption(
    "Get your free API key:"
)

st.sidebar.markdown(
    "[USDA API Registration](https://api.data.gov/signup/)"
)

st.sidebar.markdown(
    "[USDA API Documentation](https://fdc.nal.usda.gov/api-guide/)"
)


# ----------------------------------
# APPLICATION HEADER
# ----------------------------------

st.title("🥗 My Calorie Tracker")

st.caption(
    "Track your food intake and monitor your daily calories."
)

st.divider()


# ----------------------------------
# DAILY CALORIE TARGET
# ----------------------------------

st.subheader("🎯 Daily Calorie Target")

target = get_target()

new_target = st.number_input(

    "Set your daily calorie limit (kcal)",

    min_value=100,

    max_value=10000,

    value=int(target),

    step=50

)

if new_target != target:

    update_target(new_target)

    st.success(
        "Daily calorie target updated!"
    )


# ----------------------------------
# DASHBOARD
# ----------------------------------

total = get_daily_total()

remaining = new_target - total

col1, col2, col3 = st.columns(3)

with col1:

    st.metric(
        "Calories Consumed",
        f"{total:.0f} kcal"
    )

with col2:

    st.metric(
        "Daily Target",
        f"{new_target:.0f} kcal"
    )

with col3:

    st.metric(
        "Calories Remaining",
        f"{remaining:.0f} kcal"
    )

st.progress(
    max(0.0, min(total / new_target, 1.0))
)

if remaining >= 0:

    st.success(
        f"You have {remaining:.0f} kcal remaining today."
    )

else:

    st.warning(
        f"You exceeded your target by "
        f"{abs(remaining):.0f} kcal."
    )

st.divider()


# ----------------------------------
# SELECT FOOD ENTRY METHOD
# ----------------------------------

st.subheader("🔍 Add Food")

entry_method = st.radio(

    "Choose how you want to add food",

    [
        "Search USDA Food Database",
        "Enter Food Manually"
    ],

    horizontal=True

)

selected_food = None


# ----------------------------------
# USDA FOOD SEARCH
# ----------------------------------

if entry_method == "Search USDA Food Database":

    food_query = st.text_input(

        "Enter food name",

        placeholder="Example: Apple, Egg, Banana, Rice..."

    )

    if len(food_query.strip()) >= 2:

        if not api_key:

            st.warning(
                "Please enter your USDA API key "
                "in the sidebar before searching."
            )

        else:

            with st.spinner(
                "Searching USDA FoodData Central..."
            ):

                foods, api_error = search_food_api(
                    food_query.strip(),
                    api_key
                )

            if api_error:

                st.error(api_error)

            elif foods:

                options = []

                for food in foods:

                    brand = food["brand"]

                    label = food["name"]

                    if brand:

                        label += f" | {brand}"

                    label += (
                        f" | {food['calories']:.1f} "
                        f"kcal / 100 g"
                    )

                    options.append(label)

                selected_index = st.selectbox(

                    "Select a food product",

                    range(len(foods)),

                    format_func=lambda i: options[i]

                )

                selected_food = foods[selected_index]

                st.info(
                    f"""
                    **Food:** {selected_food['name']}

                    **Brand:** {selected_food['brand'] or 'Not available'}

                    **Calories:** {selected_food['calories']:.1f} kcal / 100 g

                    **Serving information:** {
                        selected_food['serving_size']
                        or 'Not provided'
                    }

                    **Serving weight:** {
                        f"{selected_food['serving_weight']:.1f} g"
                        if selected_food['serving_weight']
                        else "Not provided"
                    }
                    """
                )

            else:

                st.warning(
                    "No matching foods with calorie "
                    "information were found."
                )

    elif food_query:

        st.caption(
            "Enter at least 2 characters to search."
        )


# ----------------------------------
# MANUAL FOOD ENTRY
# ----------------------------------

else:

    st.write(
        "Enter food information manually when "
        "the food is not available in USDA."
    )

    with st.form("manual_food_form"):

        manual_name = st.text_input(

            "Food name",

            placeholder="Example: Homemade Paneer Curry"

        )

        manual_calories = st.number_input(

            "Calories per 100 grams (kcal)",

            min_value=0.0,

            max_value=10000.0,

            value=100.0,

            step=1.0

        )

        manual_submitted = st.form_submit_button(

            "Use Manual Food",

            use_container_width=True

        )

    if manual_submitted:

        if not manual_name.strip():

            st.error(
                "Please enter a food name."
            )

        else:

            selected_food = {

                "name": manual_name.strip(),

                "calories": manual_calories,

                "brand": "Manual Entry",

                "serving_weight": None,

                "serving_size": "Manually entered",

                "barcode": ""

            }

            st.success(
                f"{manual_name} selected successfully!"
            )


# ----------------------------------
# COMMON QUANTITY AND CALCULATION
# ----------------------------------

if selected_food is not None:

    st.divider()

    st.subheader(
        "Select Quantity Type"
    )

    quantity_type = st.radio(

        "Choose how you want to measure food",

        ["Grams (g)", "Units (pieces)"],

        horizontal=True

    )

    unit_count = 0

    unit_weight = 0

    quantity = 100

    # ----------------------------------
    # GRAMS
    # ----------------------------------

    if quantity_type == "Grams (g)":

        quantity = st.number_input(

            "Enter quantity in grams",

            min_value=1,

            max_value=5000,

            value=100,

            step=10

        )

        total_weight = quantity

    # ----------------------------------
    # UNITS
    # ----------------------------------

    else:

        unit_count = st.number_input(

            "Enter number of pieces",

            min_value=1,

            max_value=100,

            value=1,

            step=1

        )

        api_weight = selected_food["serving_weight"]

        if api_weight is not None:

            unit_weight = st.number_input(

                "Weight of one piece (grams)",

                min_value=1,

                max_value=5000,

                value=max(
                    1,
                    int(round(api_weight))
                ),

                step=1,

                help=(
                    "Prefilled using the USDA serving weight. "
                    "Verify that the serving corresponds "
                    "to one piece."
                )

            )

        else:

            st.warning(
                "Serving weight is not available. "
                "Please enter the weight manually."
            )

            unit_weight = st.number_input(

                "Weight of one piece (grams)",

                min_value=1,

                max_value=5000,

                value=100,

                step=1

            )

        total_weight = unit_count * unit_weight

    # ----------------------------------
    # CALCULATE CALORIES
    # ----------------------------------

    calculated_calories = (

        selected_food["calories"]
        * total_weight / 100

    )

    st.divider()

    st.subheader(
        "Calorie Calculation"
    )

    st.metric(

        "Total Calories",

        f"{calculated_calories:.1f} kcal"

    )

    st.write(
        f"**Food:** {selected_food['name']}"
    )

    st.write(
        f"**Calories per 100 g:** "
        f"{selected_food['calories']:.1f} kcal"
    )

    st.write(
        f"**Total Weight:** {total_weight:.1f} g"
    )

    if quantity_type == "Units (pieces)":

        st.write(
            f"**Quantity:** {unit_count:.0f} piece(s)"
        )

    else:

        st.write(
            f"**Quantity:** {quantity:.0f} g"
        )

    # ----------------------------------
    # ADD FOOD
    # ----------------------------------

    if st.button(

        "➕ Add Food",

        use_container_width=True

    ):

        add_food(

            selected_food,

            quantity,

            quantity_type,

            unit_count,

            unit_weight

        )

        st.success(
            "Food added successfully!"
        )

        st.rerun()


st.divider()


# ----------------------------------
# TODAY'S FOOD DIARY
# ----------------------------------

st.subheader(
    "📋 Today's Food Diary"
)

entries = get_daily_food()

if entries:

    for entry in entries:

        food_id = int(entry[0])

        food_name = entry[1]

        calories = float(entry[2])

        quantity = float(entry[3])

        quantity_type = entry[5]

        unit_count = float(entry[6])

        col1, col2, col3 = st.columns(
            [3, 2, 1]
        )

        with col1:

            st.write(
                f"**{food_name}**"
            )

        with col2:

            if quantity_type == "Units (pieces)":

                st.write(
                    f"{unit_count:.0f} piece(s) | "
                    f"{calories:.1f} kcal"
                )

            else:

                st.write(
                    f"{quantity:.0f} g | "
                    f"{calories:.1f} kcal"
                )

        with col3:

            if st.button(

                "Delete",

                key=f"delete_{food_id}"

            ):

                delete_food(food_id)

                st.rerun()

else:

    st.info(
        "No food added today."
    )


st.divider()


# ----------------------------------
# LAST 10 DAYS HISTORY
# ----------------------------------

st.subheader(
    "📅 Last 10 Days History"
)

history = get_history()

if history:

    for day, calories in history:

        st.write(
            f"**{day}** — {calories:.1f} kcal"
        )

else:

    st.info(
        "Your food history will appear here."
    )


st.divider()


# ----------------------------------
# FOOTER
# ----------------------------------

st.caption(
    "Food and nutrition information provided by "
    "USDA FoodData Central."
)

st.caption(
    "Manually entered food calories are provided "
    "by the user."
)

st.caption(
    "Food diary entries are stored in local CSV files. "
    "USDA serving information may be incomplete."
)
