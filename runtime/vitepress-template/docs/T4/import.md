# import 导入与模块组织

###### 在海龟画图中，我们使用`import` 来导入海龟模块，这样就可以使用 python 官方给我准备好的工具来绘制图形，设置画笔的等。在python中，我们可以使用`import`导入包或者模块。

## 一、什么是模块？

**一个 `.py` 文件就是一个模块。**

之前写的每个 Python 文件都是模块。例如：

```
greet.py              ← 这是一个模块
├── def say_hi():     ← 模块里的函数
├── def say_bye():    ← 模块里的函数
```

另一个文件可以通过 `import` 来使用它们：

```python
import greet

greet.say_hi()        # 调用 greet.py 里的函数
```

> [!tip]
> 模块名就是文件名去掉 `.py`。`greet.py` 的模块名就是 `greet`。

## 二、什么是包？

当模块太多时，按功能放进文件夹里。这样的文件夹叫**包**（package）。

```
tools/                ← 这是一个包（文件夹）
├── __init__.py       ← 有这个文件，Python 才认它是包
├── calc.py           ← 计算功能
└── format.py         ← 格式化功能
```

`__init__.py` 可以是空文件，也可以控制包暴露的内容。

```python
# 使用包里的模块
from tools import calc
print(calc.add(3, 5))
```

> `import 包名` 只导入 `__init__.py`，不会自动带出子模块。要访问子模块内容，需要额外导入 `import 包名.模块名` 或 `from 包名 import 模块名`。

### 包中包（子包）

包里面还能放包，用点号 `.` 连接路径。

```
tools/
├── __init__.py
├── calc.py
└── formats/               ← 子包
    ├── __init__.py
    └── markdown.py        ← 里面有 bold() 函数
```

```python
from tools.formats.markdown import bold
print(bold("hello"))
```

点号层数 = 文件夹深度。

## 三、import 的四种写法

Python 里 `import` 有四种写法：

### 3.1 `import 模块名` — 导入整个模块

```python
import math

print(math.pi)       # 3.1415926...
print(math.sqrt(16)) # 4.0
```

> 用 `math.` 前缀调用函数和变量，名字不会冲突。

### 3.2 `from 模块名 import 名字` — 只导入需要的部分

```python
from math import pi, sqrt

print(pi)            # 3.1415926...   ← 不用写 math.
print(sqrt(16))      # 4.0
```

> [!warning]
> 导入后直接使用，不用加模块名前缀。但如果两个模块有同名函数会冲突。

### 3.3 `from 模块名 import *` — 导入全部（不推荐）

```python
from math import *

print(pi)            # 能用
print(sqrt(16))      # 也能用
```

> 把所有名字导入当前空间。**不推荐**——不知道导入了什么，容易覆盖自己的变量。

### 3.4 `import 模块名 as 别名` — 起短名字

```python
import math as m

print(m.pi)          # 3.1415926...
print(m.sqrt(16))    # 4.0
```

> 模块名太长时取短别名。

### 3.5 导入包中的模块

前面四种写法针对的是单个 `.py` 文件（模块）。当要导入包（文件夹）里的模块时，用点号 `.` 分隔路径。

```
tools/                ← 包（文件夹）
├── calc.py           ← 模块
└── format.py         ← 模块
```

```python
import tools.calc                     # 导入包里的 calc 模块
tools.calc.add(3, 5)                  # 调用时加完整路径

from tools import calc                # 只导入 calc 模块
calc.add(3, 5)                        # 直接用模块名

from tools.calc import add            # 直达函数
add(3, 5)                             # 直接用函数名

import tools.calc as tc               # 取别名
tc.add(3, 5)
```

> 点号 `.` 的层数和文件夹深度一致。`tools.calc` 对应 `tools/calc.py`。

### 3.6 总结

| 写法                    | 调用方式    | 什么时候用         |
| ----------------------- | ----------- | ------------------ |
| `import math`         | `math.pi` | 最常用，推荐       |
| `from math import pi` | `pi`      | 只用少数几个功能时 |
| `from math import *`  | 直接用      | ❌ 不推荐          |
| `import math as m`    | `m.pi`    | 名字太长时         |

## 三、实现一个学生管理工具

假设我们需要实现一个**学生成绩小工具**，它有以下的功能：

- 显示每个学生的信息和成绩
- 添加新成绩
- 做全班统计（平均分、排名、等级分布）

代码较多，全写在一个文件里会难找。

我可以先分析程序包含什么：

1. 学生类的定义 — 放一个文件
2. 准备学生数据 — 放一个文件
3. 计算成绩、排名 — 放一个文件
4. 打印报表 — 放一个文件
5. 主菜单入口 — 放一个文件

这样可以拆出来 5 个部分。

把相关功能放进一个文件夹，文件结构如下：

```
student_system/
├── main.py                # 入口
├── models.py              # Student 类
├── data.py                # 学生数据
└── my_package/            # 成绩和报表
    ├── __init__.py
    ├── grade.py           # 计算
    └── report.py          # 打印
```

现在我们按前面拆出的文件结构，逐个文件实现。

### 入口：main.py

先写程序入口。它负责三件事：导入其他模块、定义主菜单循环、启动程序。

```python
# main.py - 程序入口

import models
import data
from my_package import grade, report


def main():
    students = data.load_students()

    while True:
        print("\n===== 📋 学生管理系统 =====")
        print("1. 查看学生")
        print("2. 添加成绩")
        print("3. 全班统计")
        print("0. 退出")
        choice = input("请选择: ")

        if choice == "1":
            for s in students:
                report.print_student_info(s)

        elif choice == "2":
            name = input("学生姓名: ")
            course = input("科目: ")
            score = int(input("成绩: "))
            for s in students:
                if s.name == name:
                    s.add_score(course, score)
                    print(f"✅ {name} 的 {course} 成绩已记录")
                    break
            else:
                print("❌ 找不到该学生")

        elif choice == "3":
            report.print_class_report(students)

        elif choice == "0":
            print("👋 再见!")
            break


if __name__ == '__main__':
    main()
```

`import models` 和 `import data` 导入整个模块，调用时加 `models.` 前缀。`from my_package import grade, report` 从包中导入模块。`if __name__ == '__main__'` 确保只有直接运行 `main.py` 时才进入菜单，被 `import` 时不会自动运行。

### 数据模型：models.py

程序的核心数据是"学生"。把学生的属性和行为封装在一个类里，放在单独的文件中。

```python
# models.py - Student 类的定义

class Student:
    """学生类，包含姓名和多科成绩"""

    def __init__(self, name):
        self.name = name
        self.scores = {}          # 科目 → 分数

    def add_score(self, course, score):
        self.scores[course] = score

    def get_average(self):
        if not self.scores:
            return 0
        return sum(self.scores.values()) / len(self.scores)

    def __str__(self):
        return f"{self.name} | 平均分: {self.get_average():.1f}"
```

这个文件只定义 `Student` 类，没有其他代码。把数据模型单独放一个文件是常见做法，其他地方要使用 Student 时，`from models import Student` 即可。

### 数据准备：data.py

程序需要一些预设数据来演示。把数据准备逻辑从入口文件分离出来。

```python
# data.py - 提供学生数据

from models import Student

def load_students():
    """返回几个预设的学生对象"""
    s1 = Student("小明")
    s1.add_score("语文", 92)
    s1.add_score("数学", 88)
    s1.add_score("英语", 95)

    s2 = Student("小红")
    s2.add_score("语文", 85)
    s2.add_score("数学", 96)
    s2.add_score("英语", 78)

    s3 = Student("小刚")
    s3.add_score("语文", 60)
    s3.add_score("数学", 55)
    s3.add_score("英语", 70)

    s4 = Student("小丽")
    s4.add_score("语文", 98)
    s4.add_score("数学", 92)
    s4.add_score("英语", 97)

    return [s1, s2, s3, s4]
```

`from models import Student` 只导入需要的部分。data.py 需要创建 Student 对象，但不关心 Student 类的内部实现细节。

### 包初始化：my_package/\_\_init\_\_.py

把成绩计算和报表打印归到一个包里。`__init__.py` 集中暴露包内功能。

```python
# __init__.py - 标记这是一个包

from .grade import calc_average, get_grade, calc_ranking
from .report import print_student_info, print_class_report
```

有了 `__init__.py`，Python 才把 `my_package` 当作包处理。这里集中导入了包内其他模块的关键函数，外部使用者只需 `from my_package import grade, report` 即可。

### 成绩计算：my_package/grade.py

计算平均分、等级评定、排名——这些是纯逻辑，和数据无关。

```python
# my_package/grade.py - 成绩计算功能

def calc_average(scores):
    if not scores:
        return 0
    return sum(scores) / len(scores)


def get_grade(avg):
    if avg >= 90:
        return "优"
    elif avg >= 75:
        return "良"
    elif avg >= 60:
        return "及格"
    else:
        return "不及格"


def calc_ranking(students):
    ranked = sorted(students,
                    key=lambda s: s.get_average(),
                    reverse=True)
    return ranked
```

三个函数各司其职：`calc_average` 算分数列表的平均值，`get_grade` 把平均分映射到等级，`calc_ranking` 对学生列表按平均分排序。每个函数只做一件事。

### 报表打印：my_package/report.py

打印功能依赖于 grade.py 的计算结果，因此相对导入 `from .grade import ...`。

```python
# my_package/report.py - 报表打印功能

from .grade import get_grade, calc_ranking

def print_student_info(student):
    print(f"\n{'='*30}")
    print(f"姓名: {student.name}")
    print(f"成绩:")
    for course, score in student.scores.items():
        print(f"  {course}: {score}分")
    avg = student.get_average()
    print(f"平均分: {avg:.1f}")
    print(f"等级: {get_grade(avg)}")
    print(f"{'='*30}")


def print_class_report(students):
    if not students:
        print("⚠️ 没有学生数据")
        return

    print(f"\n{'='*40}")
    print(f"📊 全班统计报表")
    print(f"{'='*40}")
    print(f"总人数: {len(students)}")

    all_avg = [s.get_average() for s in students]
    class_avg = sum(all_avg) / len(all_avg)
    print(f"全班平均分: {class_avg:.1f}")

    highest = max(students, key=lambda s: s.get_average())
    lowest = min(students, key=lambda s: s.get_average())
    print(f"最高分: {highest.name} ({highest.get_average():.1f})")
    print(f"最低分: {lowest.name} ({lowest.get_average():.1f})")

    levels = {"优": 0, "良": 0, "及格": 0, "不及格": 0}
    for s in students:
        level = get_grade(s.get_average())
        levels[level] += 1
    print(f"\n等级分布:")
    for level, count in levels.items():
        bar = "█" * count
        print(f"  {level}: {bar} ({count}人)")

    print(f"\n📈 排名:")
    ranked = calc_ranking(students)
    for i, s in enumerate(ranked, 1):
        print(f"  第{i}名: {s.name} ({s.get_average():.1f})")
    print(f"{'='*40}")
```

`from .grade import ...` 中的 `.` 表示"当前包内部"，是**相对导入**。`print_student_info` 打印单个学生信息，`print_class_report` 打印全班统计。两个函数都调用了 `grade.py` 中的计算函数。同一个包内的模块通过相对导入相互引用。

### 运行：python main.p

```bash
python main.py
```

```
===== 📋 学生管理系统 =====
1. 查看学生
2. 添加成绩
3. 全班统计
0. 退出
请选择: 3

========================================
📊 全班统计报表
========================================
总人数: 4
全班平均分: 82.5
最高分: 小丽 (95.7)
最低分: 小刚 (61.7)

等级分布:
  优: ██ (2人)
  良: █ (1人)
  及格: █ (1人)
  不及格:  (0人)

📈 排名:
  第1名: 小丽 (95.7)
  第2名: 小明 (91.7)
  第3名: 小红 (86.3)
  第4名: 小刚 (61.7)
========================================
```

### 这个项目用到的 import 写法

| 写法                                     | 位置                         | 说明                 |
| ---------------------------------------- | ---------------------------- | -------------------- |
| `import models`                        | main.py                      | 导入整个模块         |
| `import data`                          | main.py                      | 同上                 |
| `from models import Student`           | data.py                      | 只导入需要的部分     |
| `from .grade import ...`               | __init__.py, report.py | 包内相对导入         |
| `from my_package import grade, report` | main.py                      | 从包外部导入模块     |
| `if __name__ == '__main__'`            | main.py                      | 区分被导入和直接运行 |

## 四、练习题

1. 在 `main.py` 菜单里加一个选项 4，输入学生姓名后删除该学生。想想删除操作放在哪个文件里合适？
