from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.state import State, StatesGroup

router = Router()

class OnboardingStates(StatesGroup):
    waiting_company_name = State()
    waiting_industry = State()
    waiting_city = State()
    waiting_salary = State()
    waiting_communication_style = State()

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await message.answer(
        "👋 Добро пожаловать в Gwork HR Assistant!\n"
        "Я помогу вам автоматизировать подбор сотрудников.\n\n"
        "Для начала настроим профиль компании.\n"
        "Как называется ваша компания?"
    )
    await state.set_state(OnboardingStates.waiting_company_name)

@router.message(OnboardingStates.waiting_company_name)
async def process_company_name(message: Message, state: FSMContext):
    await state.update_data(company_name=message.text)
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Салон красоты")],
            [KeyboardButton(text="Кафе/ресторан")],
            [KeyboardButton(text="Розничная торговля")],
            [KeyboardButton(text="Другое")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        "Отлично! В какой сфере работает ваша компания?",
        reply_markup=keyboard
    )
    await state.set_state(OnboardingStates.waiting_industry)