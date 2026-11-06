# -*- coding: utf-8 -*-
"""训练数据管理路由（Iter 2）

数据层只有本地 Excel 一条路径：
  - 页面上传 .xlsx → /training/api/import-excel
  - 扫描 data/ 目录 → /training/api/import-excel-dir
记录本身仍支持增删改查。
"""

import os
import sys
import tempfile
import time
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from models.training_record import SessionLocal, TrainingRecordExcel
from utils.excel_importer import import_files

training_data_bp = Blueprint('training_data', __name__, url_prefix='/training')


@training_data_bp.route('/')
def index():
    return render_template('training_data.html')


# ---------------- 记录查询 ----------------
@training_data_bp.route('/api/records', methods=['GET'])
def get_records():
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    keyword = (request.args.get('q') or '').strip()

    session = SessionLocal()
    try:
        query = session.query(TrainingRecordExcel)
        if keyword:
            if keyword.isdigit():
                query = query.filter(TrainingRecordExcel.id == int(keyword))
            else:
                query = query.filter(TrainingRecordExcel.exercise_type.like(f'%{keyword}%'))

        total = query.count()
        records = (query.order_by(TrainingRecordExcel.start_time.desc())
                   .limit(per_page)
                   .offset((page - 1) * per_page)
                   .all())

        return jsonify({
            'success': True,
            'data': [r.to_dict() for r in records],
            'total': total,
            'page': page,
            'per_page': per_page,
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        session.close()


@training_data_bp.route('/api/record', methods=['POST'])
def add_record():
    session = SessionLocal()
    try:
        data = request.get_json()
        for field in ('exercise_type', 'duration_seconds', 'start_time', 'end_time'):
            if field not in data:
                return jsonify({'success': False, 'message': f'缺少必填字段: {field}'}), 400

        try:
            start_time = datetime.strptime(data['start_time'], '%Y-%m-%d %H:%M:%S')
            end_time = datetime.strptime(data['end_time'], '%Y-%m-%d %H:%M:%S')
        except ValueError as e:
            return jsonify({'success': False, 'message': f'时间格式错误: {e}'}), 400

        now_ts = int(time.time())
        record = TrainingRecordExcel(
            user_id=data.get('user_id', 'default_user'),
            exercise_type=data['exercise_type'],
            duration_seconds=int(data['duration_seconds']),
            start_time=start_time,
            end_time=end_time,
            calories=int(data['calories']) if data.get('calories') else None,
            distance_meters=float(data['distance_meters']) if data.get('distance_meters') else None,
            avg_heart_rate=int(data['avg_heart_rate']) if data.get('avg_heart_rate') else None,
            max_heart_rate=int(data['max_heart_rate']) if data.get('max_heart_rate') else None,
            add_ts=now_ts,
            last_modify_ts=now_ts,
            data_source=data.get('data_source', 'manual_import'),
        )
        session.add(record)
        session.commit()
        session.refresh(record)

        return jsonify({'success': True, 'message': '添加成功', 'data': record.to_dict()})
    except Exception as e:
        session.rollback()
        return jsonify({'success': False, 'message': f'添加失败: {e}'}), 500
    finally:
        session.close()


@training_data_bp.route('/api/record/<int:record_id>', methods=['GET'])
def get_record(record_id):
    session = SessionLocal()
    try:
        record = session.query(TrainingRecordExcel).filter(TrainingRecordExcel.id == record_id).first()
        if not record:
            return jsonify({'success': False, 'message': '记录不存在'}), 404
        return jsonify({'success': True, 'data': record.to_dict()})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        session.close()


@training_data_bp.route('/api/record/<int:record_id>', methods=['PUT'])
def update_record(record_id):
    session = SessionLocal()
    try:
        record = session.query(TrainingRecordExcel).filter(TrainingRecordExcel.id == record_id).first()
        if not record:
            return jsonify({'success': False, 'message': '记录不存在'}), 404

        data = request.get_json()
        if 'user_id' in data:
            record.user_id = data['user_id']
        if 'exercise_type' in data:
            record.exercise_type = data['exercise_type']
        if 'duration_seconds' in data:
            record.duration_seconds = int(data['duration_seconds'])
        if 'start_time' in data:
            record.start_time = datetime.strptime(data['start_time'], '%Y-%m-%d %H:%M:%S')
        if 'end_time' in data:
            record.end_time = datetime.strptime(data['end_time'], '%Y-%m-%d %H:%M:%S')
        if 'calories' in data:
            record.calories = int(data['calories']) if data['calories'] else None
        if 'distance_meters' in data:
            record.distance_meters = float(data['distance_meters']) if data['distance_meters'] else None
        if 'avg_heart_rate' in data:
            record.avg_heart_rate = int(data['avg_heart_rate']) if data['avg_heart_rate'] else None
        if 'max_heart_rate' in data:
            record.max_heart_rate = int(data['max_heart_rate']) if data['max_heart_rate'] else None
        if 'data_source' in data:
            record.data_source = data['data_source']

        record.last_modify_ts = int(time.time())
        session.commit()
        session.refresh(record)

        return jsonify({'success': True, 'message': '更新成功', 'data': record.to_dict()})
    except Exception as e:
        session.rollback()
        return jsonify({'success': False, 'message': f'更新失败: {e}'}), 500
    finally:
        session.close()


@training_data_bp.route('/api/record/<int:record_id>', methods=['DELETE'])
def delete_record(record_id):
    session = SessionLocal()
    try:
        record = session.query(TrainingRecordExcel).filter(TrainingRecordExcel.id == record_id).first()
        if not record:
            return jsonify({'success': False, 'message': '记录不存在'}), 404
        session.delete(record)
        session.commit()
        return jsonify({'success': True, 'message': '删除成功'})
    except Exception as e:
        session.rollback()
        return jsonify({'success': False, 'message': f'删除失败: {e}'}), 500
    finally:
        session.close()


@training_data_bp.route('/api/exercise_types', methods=['GET'])
def get_exercise_types():
    session = SessionLocal()
    try:
        types_list = [t[0] for t in session.query(TrainingRecordExcel.exercise_type)
                      .distinct().order_by(TrainingRecordExcel.exercise_type).all()]
        common = ['跑步', '骑行', '游泳', '健走', '爬山', '徒步', '瑜伽',
                  '力量训练', '篮球', '足球', '羽毛球', '网球', '乒乓球']
        for ct in common:
            if ct not in types_list:
                types_list.append(ct)
        return jsonify({'success': True, 'data': types_list})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        session.close()


@training_data_bp.route('/api/data_sources', methods=['GET'])
def get_data_sources():
    return jsonify({'success': True, 'data': ['manual_import', 'excel_import']})


# ---------------- Excel 导入 ----------------
@training_data_bp.route('/api/import-excel', methods=['POST'])
def import_excel():
    """上传一个 .xlsx 并导入"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '没有收到文件'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'success': False, 'message': '文件名为空'}), 400
    if not f.filename.lower().endswith(config.EXCEL_ALLOWED_EXT):
        return jsonify({'success': False, 'message': f'只支持 {config.EXCEL_ALLOWED_EXT} 文件'}), 400

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=config.EXCEL_ALLOWED_EXT)
        os.close(fd)
        f.save(tmp_path)
        result = import_files([tmp_path])
        return jsonify({
            'success': True,
            'message': f"导入完成：新增 {result['added']} 条，跳过重复 {result['skipped']} 条",
            **result,
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'导入失败: {e}'}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


# 注：扫描 data/ 目录的入口已从页面移除（Iter 2 数据层 = 页面上传 Excel）

